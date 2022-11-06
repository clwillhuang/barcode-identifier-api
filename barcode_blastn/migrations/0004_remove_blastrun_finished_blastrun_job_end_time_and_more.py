# Generated by Django 4.1.2 on 2022-11-06 23:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('barcode_blastn', '0003_blastrun_finished'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='blastrun',
            name='finished',
        ),
        migrations.AddField(
            model_name='blastrun',
            name='job_end_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='blastrun',
            name='job_start_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='blastrun',
            name='job_status',
            field=models.CharField(choices=[('UNK', 'UNKNOWN'), ('DEN', 'DENIED'), ('QUE', 'QUEUED'), ('STA', 'RUNNING'), ('ERR', 'ERRORED'), ('FIN', 'FINISHED')], default='UNK', max_length=3),
        ),
    ]
