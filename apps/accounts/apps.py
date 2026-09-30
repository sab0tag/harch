from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    verbose_name = "Користувачі"

    def ready(self):
        from . import signins

        signins.connect()     # admins hear about sign-ins from new addresses
