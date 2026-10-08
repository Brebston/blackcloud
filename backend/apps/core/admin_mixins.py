from django.contrib import admin

from .audit import audit


class AuditedAdminMixin:
    """Кожна зміна через Django admin потрапляє в незмінний журнал аудиту."""

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        fields = list(form.changed_data) if form is not None else []
        audit(request, "djadmin.change" if change else "djadmin.add", target=f"{obj._meta.label}:{obj.pk}", fields=fields)

    def delete_model(self, request, obj):
        target = f"{obj._meta.label}:{obj.pk}"
        super().delete_model(request, obj)
        audit(request, "djadmin.delete", target=target)

    def delete_queryset(self, request, queryset):
        targets = [f"{o._meta.label}:{o.pk}" for o in queryset[:200]]
        super().delete_queryset(request, queryset)
        audit(request, "djadmin.delete_bulk", target=queryset.model._meta.label, ids=targets)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Лише перегляд: змінювати ці записи можна тільки через API з усіма перевірками."""

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD")

    def has_delete_permission(self, request, obj=None):
        return False
