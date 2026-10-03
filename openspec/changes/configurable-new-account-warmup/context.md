# New-account warm-up enrollment

Operators who opt accounts into limit warm-up can choose to enroll future OAuth and auth.json accounts automatically. The default remains false so existing installations keep their previous enrollment behavior. The global warm-up switch remains independent and defaults off; enrollment alone sends no traffic.

This is a T3 behavior setting stored in `dashboard_settings.limit_warmup_auto_enable_new_accounts`. A constant cannot express operators who deliberately enroll individual accounts versus operators who warm every future account. No environment setting is added.

For example, enabling auto-enrollment enrolls the next imported account while preserving an existing account that the operator explicitly opted out. Disabling it affects only future accounts. Reauthentication preserves existing preferences.

The forward migration adds a non-null boolean with a false server default and does not modify any account rows. It is based on current upstream's single Alembic head, independently of the fork's migration history.
