from django import forms
from django.contrib.auth import password_validation


class AcceptInvitationForm(forms.Form):
    full_name = forms.CharField(label="Nom complet", max_length=150)
    password1 = forms.CharField(label="Mot de passe", widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(
        label="Confirmation du mot de passe", widget=forms.PasswordInput, strip=False
    )

    def __init__(self, *args, invitation, **kwargs):
        self.invitation = invitation
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Les deux mots de passe ne correspondent pas.")
        if p1:
            from .models import User

            candidate = User(email=self.invitation.email, full_name=data.get("full_name", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data
