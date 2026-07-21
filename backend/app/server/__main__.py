from app.runtime_environment import load_runtime_environment


# Load local-only settings before importing the application. Provider URL
# policy and other module-level configuration must see the final environment.
load_runtime_environment()

from app.server.application import main  # noqa: E402


main()
