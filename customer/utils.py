from django.contrib.auth import get_user_model


def get_points_balance(user: get_user_model()) -> int:
    """Return the current points balance for the given user.

    The dashboard currently displays a placeholder value. Centralising
    the logic here keeps the login API and dashboard in sync and makes it
    easy to implement real point tracking later.
    """

    return 0

