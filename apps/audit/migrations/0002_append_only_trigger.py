"""Trigger PostgreSQL : le journal d'audit refuse tout UPDATE et tout DELETE.

Même un accès SQL direct (hors Django) ne peut pas modifier l'historique.
Une éventuelle purge légale (durée de conservation CNDP) devra passer par une
procédure dédiée, explicite et elle-même tracée.
"""

from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION audit_journalaudit_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Le journal d''audit est en ajout seul (% interdit).', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_journalaudit_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_journalaudit
    FOR EACH ROW EXECUTE FUNCTION audit_journalaudit_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS audit_journalaudit_no_update_delete ON audit_journalaudit;
DROP FUNCTION IF EXISTS audit_journalaudit_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
