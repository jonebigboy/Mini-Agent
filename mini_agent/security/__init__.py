"""Security module for Mini-Agent."""

try:
    from mini_agent.security.middleware import SecurityMiddleware
except ModuleNotFoundError:
    pass

__all__ = ["SecurityMiddleware"]
