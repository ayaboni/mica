import os

base_dir = os.path.dirname(os.path.abspath(__file__))

# Folders to create
folders = [
    os.path.join(base_dir, "core", "templates", "core"),
    os.path.join(base_dir, "core", "static", "core", "css"),
    os.path.join(base_dir, "core", "static", "core", "js"),
    os.path.join(base_dir, "core", "static", "core", "images"),
]

for folder in folders:
    os.makedirs(folder, exist_ok=True)

# Files & Boilerplate
files = {
    os.path.join(base_dir, "core", "models.py"): """from django.db import models

class Item(models.Model):
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    available = models.BooleanField(default=True)

    def __str__(self):
        return self.name
""",

    os.path.join(base_dir, "core", "views.py"): """from django.shortcuts import render
from .models import Item

def home(request):
    items = Item.objects.all()
    return render(request, 'core/index.html', {'items': items})
""",

    os.path.join(base_dir, "core", "urls.py"): """from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
]
""",

    os.path.join(base_dir, "core", "templates", "core", "base.html"): """{% load static %}
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{% block title %}Django App{% endblock %}</title>
    <link rel="stylesheet" href="{% static 'core/css/style.css' %}">
</head>
<body>
    <header><h1>Welcome</h1></header>
    <main>{% block content %}{% endblock %}</main>
    <script src="{% static 'core/js/main.js' %}"></script>
</body>
</html>
""",

    os.path.join(base_dir, "core", "templates", "core", "index.html"): """{% extends 'core/base.html' %}
{% block content %}
<h2>Home Page Working!</h2>
{% endblock %}
""",

    # Page Templates
    os.path.join(base_dir, "core", "templates", "core", "index.html"): "{% extends 'core/base.html' %}\n{% block content %}<h1>Home Page</h1>{% endblock %}",
    os.path.join(base_dir, "core", "templates", "core", "about.html"): "{% extends 'core/base.html' %}\n{% block content %}<h1>About Page</h1>{% endblock %}",
    os.path.join(base_dir, "core", "templates", "core", "contact.html"): "{% extends 'core/base.html' %}\n{% block content %}<h1>Contact Page</h1>{% endblock %}",
    os.path.join(base_dir, "core", "templates", "core", "register.html"): "{% extends 'core/base.html' %}\n{% block content %}<h1>Register</h1>{% endblock %}",
    os.path.join(base_dir, "core", "templates", "core", "login.html"): "{% extends 'core/base.html' %}\n{% block content %}<h1>Login</h1>{% endblock %}",
    os.path.join(base_dir, "core", "templates", "core", "student-dashboard.html"): "{% extends 'core/base.html' %}\n{% block content %}<h1>Student Dashboard</h1>{% endblock %}",
    os.path.join(base_dir, "core", "templates", "core", "staff-admin.html"): "{% extends 'core/base.html' %}\n{% block content %}<h1>Dashboard</h1>{% endblock %}",


    os.path.join(base_dir, "core", "static", "core", "css", "style.css"): "body { font-family: sans-serif; padding: 20px; }",
    os.path.join(base_dir, "core", "static", "core", "js", "main.js"): "console.log('Django script active.');",
}

for path, content in files.items():
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

print("App files successfully built!")