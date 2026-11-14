from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="ParseJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=120)),
                ("code", models.CharField(max_length=64, unique=True)),
                ("status", models.CharField(default="待解析", max_length=32)),
                ("source_uri", models.CharField(max_length=255)),
                ("parsed", models.IntegerField(default=0)),
                ("landed", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["id"]},
        ),
    ]
