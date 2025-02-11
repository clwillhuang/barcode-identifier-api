# Generated by Django 4.1.3 on 2024-11-24 23:36

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('barcode_blastn', '0003_alter_nuccoresequence_genbank_modification_date_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='nuccoresequence',
            name='data_source',
            field=models.CharField(choices=[('GB', 'GenBank'), ('IM', 'Import')], default='GB', help_text='The source of this sequence.', max_length=2),
        ),
    ]
