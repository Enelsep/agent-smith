class ConfigError(Exception):
    """The runtime configuration could not be resolved.

    Messages must name environment variables, never their values: this
    exception reaches logs and stderr.
    """
