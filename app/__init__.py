__version__ = "1.0.2"  # x-release-please-version
__all__ = ["app", "__version__"]


def __getattr__(name: str):
    if name == "app":
        from app.main import app as fastapi_app

        return fastapi_app
    raise AttributeError(name)
