"""Unit tests for the UnifiedMCPClient supporting Stdio and HTTP transports."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_smith.mcp.client import MCPConnectionError, UnifiedMCPClient
from agent_smith.mcp.protocol import MCPToolDefinition


class TestUnifiedMCPClientInitialization:
    def test_init_raises_value_error_if_neither_command_nor_url_provided(
        self,
    ) -> None:
        with pytest.raises(
            ValueError,
            match=r"Either 'command' \(stdio\) or 'url' \(streamable http\) must be provided\.",
        ):
            UnifiedMCPClient()

    def test_init_with_command(self) -> None:
        client = UnifiedMCPClient(
            command="python", args=["server.py"], env={"KEY": "VAL"}
        )
        assert client.command == "python"
        assert client.args == ["server.py"]
        assert client.env == {"KEY": "VAL"}
        assert client.url is None

    def test_init_with_url(self) -> None:
        client = UnifiedMCPClient(url="http://localhost:8000/mcp")
        assert client.url == "http://localhost:8000/mcp"
        assert client.command is None
        assert client.args == []


@pytest.mark.asyncio
class TestUnifiedMCPClientConnectAndDisconnect:
    @patch("agent_smith.mcp.client.ClientSession")
    @patch("agent_smith.mcp.client.stdio_client")
    async def test_connect_stdio_success(
        self, mock_stdio_client: MagicMock, mock_client_session: MagicMock
    ) -> None:
        mock_transport = (MagicMock(), MagicMock())
        mock_stdio_client.return_value.__aenter__.return_value = mock_transport

        mock_session = AsyncMock()
        mock_client_session.return_value.__aenter__.return_value = mock_session

        client = UnifiedMCPClient(command="uvx", args=["mcp-server-git"])
        await client.connect()

        mock_stdio_client.assert_called_once()
        mock_session.initialize.assert_awaited_once()
        assert client._session == mock_session
        assert client._exit_stack is not None

    @patch("agent_smith.mcp.client.ClientSession")
    @patch("agent_smith.mcp.client.streamable_http_client")
    async def test_connect_http_success(
        self, mock_http_client: MagicMock, mock_client_session: MagicMock
    ) -> None:
        mock_transport = (MagicMock(), MagicMock(), None)
        mock_http_client.return_value.__aenter__.return_value = mock_transport

        mock_session = AsyncMock()
        mock_client_session.return_value.__aenter__.return_value = mock_session

        client = UnifiedMCPClient(url="http://localhost:8000/mcp")
        await client.connect()

        mock_http_client.assert_called_once_with("http://localhost:8000/mcp")
        mock_session.initialize.assert_awaited_once()
        assert client._session == mock_session

    @patch("agent_smith.mcp.client.stdio_client")
    async def test_connect_failure_raises_mcp_connection_error(
        self, mock_stdio_client: MagicMock
    ) -> None:
        mock_stdio_client.side_effect = Exception("Subprocess failed to launch")

        client = UnifiedMCPClient(command="invalid-cmd")
        with pytest.raises(MCPConnectionError) as exc_info:
            await client.connect()

        assert "Connection failed" in str(exc_info.value)
        assert client._session is None
        assert client._exit_stack is None

    async def test_disconnect_cleans_up_resources(self) -> None:
        client = UnifiedMCPClient(command="test")
        mock_exit_stack = AsyncMock()
        client._exit_stack = mock_exit_stack
        client._session = AsyncMock()

        await client.disconnect()

        mock_exit_stack.aclose.assert_awaited_once()
        assert client._exit_stack is None
        assert client._session is None


@pytest.mark.asyncio
class TestUnifiedMCPClientOperations:
    async def test_list_tools_success(self) -> None:
        client = UnifiedMCPClient(command="test")

        mock_tool = MagicMock()
        mock_tool.name = "search"
        mock_tool.description = "Search codebase"
        mock_tool.input_schema = {"type": "object"}

        mock_response = MagicMock()
        mock_response.tools = [mock_tool]

        mock_session = AsyncMock()
        mock_session.list_tools.return_value = mock_response
        client._session = mock_session

        tools = await client.list_tools()

        assert len(tools) == 1
        assert isinstance(tools[0], MCPToolDefinition)
        assert tools[0].name == "search"
        assert tools[0].description == "Search codebase"
        assert tools[0].input_schema == {"type": "object"}

    @patch.object(UnifiedMCPClient, "connect", new_callable=AsyncMock)
    @patch.object(UnifiedMCPClient, "disconnect", new_callable=AsyncMock)
    async def test_list_tools_retries_and_succeeds(
        self, mock_disconnect: AsyncMock, mock_connect: AsyncMock
    ) -> None:
        client = UnifiedMCPClient(command="test", max_retries=2)

        mock_tool = MagicMock()
        mock_tool.name = "tool1"
        mock_tool.description = "desc"
        mock_tool.input_schema = {}

        mock_session = AsyncMock()
        mock_session.list_tools.side_effect = [
            Exception("Transient error"),
            MagicMock(tools=[mock_tool]),
        ]
        client._session = mock_session

        tools = await client.list_tools()

        assert len(tools) == 1
        assert mock_session.list_tools.call_count == 2
        assert mock_disconnect.await_count == 1

    @patch.object(UnifiedMCPClient, "connect", new_callable=AsyncMock)
    @patch.object(UnifiedMCPClient, "disconnect", new_callable=AsyncMock)
    async def test_list_tools_exceeds_max_retries_raises_error(
        self, mock_disconnect: AsyncMock, mock_connect: AsyncMock
    ) -> None:
        client = UnifiedMCPClient(command="test", max_retries=1)

        mock_session = AsyncMock()
        mock_session.list_tools.side_effect = Exception("Persistent failure")
        client._session = mock_session

        with pytest.raises(MCPConnectionError) as exc_info:
            await client.list_tools()

        assert "Persistent failure listing tools" in str(exc_info.value)
        assert mock_session.list_tools.call_count == 2
        assert mock_disconnect.await_count == 2

    async def test_call_tool_success_formats_mixed_content(self) -> None:
        client = UnifiedMCPClient(command="test")

        mock_content_text = MagicMock()
        mock_content_text.text = "Line 1"
        mock_content_raw = "Line 2"

        mock_result = MagicMock()
        mock_result.content = [mock_content_text, mock_content_raw]

        mock_session = AsyncMock()
        mock_session.call_tool.return_value = mock_result
        client._session = mock_session

        output = await client.call_tool("exec_cmd", {"cmd": "ls"})

        assert output == "Line 1\nLine 2"
        mock_session.call_tool.assert_awaited_once_with("exec_cmd", {"cmd": "ls"})

    @patch.object(UnifiedMCPClient, "connect", new_callable=AsyncMock)
    @patch.object(UnifiedMCPClient, "disconnect", new_callable=AsyncMock)
    async def test_call_tool_persistent_failure_returns_observation(
        self, mock_disconnect: AsyncMock, mock_connect: AsyncMock
    ) -> None:
        client = UnifiedMCPClient(command="test", max_retries=1)

        mock_session = AsyncMock()
        mock_session.call_tool.side_effect = Exception("Connection lost")
        client._session = mock_session

        output = await client.call_tool("broken_tool", {})

        assert output.startswith(
            "Observation: Error communicating with MCP server while "
            "calling tool 'broken_tool': Connection lost"
        )
        assert mock_session.call_tool.call_count == 2
        assert mock_disconnect.await_count == 1
