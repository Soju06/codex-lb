__version__ = "0.2.0"
__all__ = ["app", "__version__"]


def __getattr__(name: str):
    if name == "app":
        from app.main import app as fastapi_app

        return fastapi_app
    raise AttributeError(name)
