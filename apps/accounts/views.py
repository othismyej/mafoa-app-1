from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import log

from .forms import AcceptInvitationForm
from .models import Invitation, User
from .permissions import DIRECTION_ROLES, role_required


@role_required(*DIRECTION_ROLES)
def dashboard(request):
    from apps.recruitment.models import Candidature, Concours, OffreEmploi

    context = {
        "concours_count": Concours.objects.count(),
        "offres_count": OffreEmploi.objects.count(),
        "candidatures_count": Candidature.objects.count(),
        "a_verifier_count": Candidature.objects.filter(
            statut=Candidature.Statut.A_VERIFIER
        ).count(),
        "concours": Concours.objects.select_related("organisation").order_by("-date_limite")[:10],
    }
    return render(request, "dashboard/index.html", context)


def accept_invitation(request, token: str):
    invitation = get_object_or_404(Invitation, token=token)
    if not invitation.is_usable:
        raise Http404("Invitation invalide ou expirée.")
    if User.objects.filter(email__iexact=invitation.email).exists():
        raise Http404("Un compte existe déjà pour cet email.")

    form = AcceptInvitationForm(request.POST or None, invitation=invitation)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = User.objects.create_user(
                email=invitation.email,
                password=form.cleaned_data["password1"],
                full_name=form.cleaned_data["full_name"],
                role=invitation.role,
            )
            invitation.accepted_at = timezone.now()
            invitation.accepted_user = user
            invitation.save(update_fields=["accepted_at", "accepted_user"])
            log(AuditAction.INVITATION_ACCEPTED, actor=user, obj=invitation, request=request)
        messages.success(
            request,
            "Compte créé. Connectez-vous puis configurez la double authentification.",
        )
        return redirect("account_login")
    return render(
        request, "accounts/accept_invitation.html", {"form": form, "invitation": invitation}
    )
