"""Service-layer errors.

Services raise these; the API layer maps them to status codes and the UI layer renders
them into a component. Neither of those concerns leaks in here — a service must be
callable from the worker container, where there is no request and no template.
"""

from __future__ import annotations


class ServiceError(Exception):
    status_code = 400


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class RightsError(ServiceError):
    """Generation blocked because likeness consent is missing.

    Its own class because it is the one refusal in the system that is a product
    feature rather than a validation failure — PRODUCT.md gap #3.
    """

    status_code = 422
