import factory

from apps.accounts.models import Role, User
from apps.orgstructure.models import Department


class DepartmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Department
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Відділення {n}")


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    first_name = "Тест"
    last_name = factory.Sequence(lambda n: f"Користувач{n}")
    role = Role.DIETICIAN
    password = factory.django.Password("pass12345!")

    @factory.lazy_attribute
    def department(self):
        return DepartmentFactory() if self.role == Role.DEPT_NURSE else None
