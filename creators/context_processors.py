from django.db import OperationalError, ProgrammingError, connection

from .models import CreatorMeta


def creator_onboarding(request):
    if not request.user.is_authenticated or not request.user.is_creator:
        return {}
    try:
        if "creators_creatormeta" not in connection.introspection.table_names():
            return {}
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                connection.cursor(), "creators_creatormeta"
            )
        }
        required_columns = {
            "country",
            "primary_niches",
            "platforms",
            "onboarding_step",
            "onboarding_completed",
            "onboarding_completion_percent",
        }
        if not required_columns.issubset(columns):
            return {}
        meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
        meta.refresh_onboarding_progress(persist=True)
    except (OperationalError, ProgrammingError):
        return {}
    return {"creator_onboarding": meta.onboarding_status()}
