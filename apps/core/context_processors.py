from django.conf import settings


def site(request):
    return {"HOSPITAL_NAME": settings.HOSPITAL_NAME}
