"""What a caller must say about the task to be solved."""

from pydantic import BaseModel, ConfigDict


class TaskSpec(BaseModel):
    """One task, described well enough to run it.

    Frozen for the reason `LLMResponse` is frozen: these are a run's inputs,
    a record rather than state anyone should edit while the loop turns.

    Four bare strings in a signature would swap silently at a call site. This
    model names them, and it is the written contract between CORE-4 and the two
    CLIs that build one.

    `system_prompt` is carried rather than derived because
    `SolutionOutput.system_prompt` requires it verbatim for provenance.

    `benchmark` is a plain string, matching `SolutionOutput.benchmark`. A
    `Literal` here would be stricter than the contract it feeds.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    benchmark: str
    system_prompt: str
    task_prompt: str
