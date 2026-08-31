from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trading", "0002_dailypnlrecord"),
    ]

    operations = [
        migrations.AddField(
            model_name="dailypnlrecord",
            name="principal",
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name="dailypnlrecord",
            name="locked",
            field=models.BooleanField(default=False),
        ),
    ]
