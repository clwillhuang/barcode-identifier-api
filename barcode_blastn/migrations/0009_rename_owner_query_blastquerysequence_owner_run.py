# Generated by Django 4.1.2 on 2022-11-21 02:05

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('barcode_blastn', '0008_blastquerysequence'),
    ]

    operations = [
        migrations.RenameField(
            model_name='blastquerysequence',
            old_name='owner_query',
            new_name='owner_run',
        ),
    ]
