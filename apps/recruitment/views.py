from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from apps.accounts.permissions import DIRECTION_ROLES, role_required
from apps.audit.models import AuditAction
from apps.audit.services import log

from .models import Concours
from .selectors import concours_stats


@require_GET
@role_required(*DIRECTION_ROLES)
def concours_detail(request, pk: int):
    """Fiche d'un concours : postes et répartition des candidatures (spec KAN-9)."""
    concours = get_object_or_404(Concours.objects.select_related("organisation"), pk=pk)
    stats = concours_stats(concours)
    log(AuditAction.VIEW, obj=concours, request=request)
    return render(request, "recruitment/concours_detail.html", {"stats": stats})
