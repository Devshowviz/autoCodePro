from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trading", "0003_dailypnlrecord_principal_locked"),
    ]

    operations = [
        migrations.RemoveField(model_name="dailypnlrecord", name="principal"),
        migrations.RemoveField(model_name="dailypnlrecord", name="locked"),
        migrations.AddField(
            model_name="dailypnlrecord",
            name="equity_base",
            field=models.FloatField(default=0),
        ),
    ]
