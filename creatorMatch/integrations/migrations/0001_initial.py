from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SocialAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(choices=[('instagram', 'Instagram'), ('youtube', 'YouTube'), ('tiktok', 'TikTok'), ('twitch', 'Twitch')], max_length=30)),
                ('external_account_id', models.CharField(max_length=255)),
                ('username', models.CharField(blank=True, max_length=255)),
                ('display_name', models.CharField(blank=True, max_length=255)),
                ('profile_url', models.URLField(blank=True)),
                ('profile_picture_url', models.URLField(blank=True)),
                ('scopes', models.JSONField(blank=True, default=list)),
                ('account_metadata', models.JSONField(blank=True, default=dict)),
                ('connection_status', models.CharField(choices=[('connected', 'Connected'), ('expired', 'Expired'), ('error', 'Error'), ('disconnected', 'Disconnected')], default='connected', max_length=20)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('last_sync_status', models.CharField(choices=[('idle', 'Idle'), ('queued', 'Queued'), ('running', 'Running'), ('success', 'Success'), ('failed', 'Failed')], default='idle', max_length=20)),
                ('last_error', models.TextField(blank=True)),
                ('connected_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('disconnected_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='social_accounts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('user', 'provider')},
            },
        ),
        migrations.CreateModel(
            name='SocialMetricSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(choices=[('instagram', 'Instagram'), ('youtube', 'YouTube'), ('tiktok', 'TikTok'), ('twitch', 'Twitch')], max_length=30)),
                ('captured_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('period_start', models.DateTimeField(blank=True, null=True)),
                ('period_end', models.DateTimeField(blank=True, null=True)),
                ('metrics', models.JSONField(default=dict)),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('social_account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='metric_snapshots', to='integrations.socialaccount')),
            ],
            options={
                'ordering': ['-captured_at'],
            },
        ),
        migrations.CreateModel(
            name='SocialAccountToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('access_token_encrypted', models.TextField()),
                ('refresh_token_encrypted', models.TextField(blank=True)),
                ('token_type', models.CharField(blank=True, max_length=40)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('token_metadata', models.JSONField(blank=True, default=dict)),
                ('invalidated_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('social_account', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='token', to='integrations.socialaccount')),
            ],
        ),
        migrations.CreateModel(
            name='OAuthState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(choices=[('instagram', 'Instagram'), ('youtube', 'YouTube'), ('tiktok', 'TikTok'), ('twitch', 'Twitch')], max_length=30)),
                ('state', models.CharField(max_length=255, unique=True)),
                ('redirect_path', models.CharField(default='/creators/settings/', max_length=255)),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='oauth_states', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='IntegrationSyncRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('idle', 'Idle'), ('queued', 'Queued'), ('running', 'Running'), ('success', 'Success'), ('failed', 'Failed')], default='queued', max_length=20)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('error_message', models.TextField(blank=True)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('social_account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sync_runs', to='integrations.socialaccount')),
            ],
        ),
        migrations.AddIndex(
            model_name='socialaccount',
            index=models.Index(fields=['provider', 'connection_status'], name='integrations_provider_853bb4_idx'),
        ),
    ]
