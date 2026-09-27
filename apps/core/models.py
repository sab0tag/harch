from django.db import models


class Heartbeat(models.Model):
    """Last time a background job finished (S5-05). One row per job name, overwritten on each run."""

    name = models.CharField("завдання", max_length=50, primary_key=True)
    at = models.DateTimeField("останній успішний запуск")

    class Meta:
        verbose_name = "сигнал життя"
        verbose_name_plural = "сигнали життя"

    def __str__(self):
        return f"{self.name} @ {self.at:%Y-%m-%d %H:%M}"
