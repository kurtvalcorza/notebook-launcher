"""Non-production configuration for the local container acceptance smoke test."""

c = get_config()  # noqa: F821 - provided by Jupyter at config load time
c.IdentityProvider.token = "notebook-launcher-acceptance"
c.ServerApp.open_browser = False
c.ServerApp.allow_remote_access = True
c.ServerApp.ip = "0.0.0.0"
