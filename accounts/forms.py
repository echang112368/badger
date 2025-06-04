from django import forms
from django.contrib.auth.forms import AuthenticationForm

class CustomLoginForm(AuthenticationForm):
    username = forms.CharField()
    password = forms.CharField(widget = forms.PasswordInput)
    