import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """Разрешаем пару № 0 — кураторский час."""

    dependencies = [
        ("portal", "0003_submissions"),
    ]

    operations = [
        migrations.AlterField(
            model_name="scheduleitem",
            name="lesson_number",
            field=models.PositiveSmallIntegerField(
                help_text="0 — кураторский час",
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(10),
                ],
                verbose_name="Номер пары",
            ),
        ),
    ]
