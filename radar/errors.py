class RadarError(Exception):
    """Base error for rule violations shown to the user."""


class PermissionDenied(RadarError):
    pass


class Blocked(RadarError):
    pass
