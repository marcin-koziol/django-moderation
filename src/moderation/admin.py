from django.contrib import admin, messages
from django.db.models.query_utils import Q
from django.forms.models import ModelForm
from django.contrib.admin.filterspecs import FilterSpec, ChoicesFilterSpec
from django.contrib.contenttypes.models import ContentType
from django.core import urlresolvers
from django.http import HttpResponseRedirect
from django.utils.translation import ugettext, ugettext_lazy as _
import django

from moderation.models import ModeratedObject, MODERATION_DRAFT_STATE,\
    MODERATION_STATUS_PENDING, MODERATION_STATUS_REJECTED,\
    MODERATION_STATUS_APPROVED

from django.utils.translation import ugettext as _
from moderation.forms import make_moderatedform_from_modelform
from moderation.helpers import automoderate
from moderation.diff import get_changes_between_models


def approve_objects(modeladmin, request, queryset):
    for obj in queryset:
        moderation_message = obj.approve(moderated_by=request.user)
        messages.add_message(request, messages.INFO, moderation_message)

approve_objects.short_description = _("Approve selected moderated objects")


def reject_objects(modeladmin, request, queryset):
    for obj in queryset:
        moderation_message = obj.reject(moderated_by=request.user)
        messages.add_message(request, messages.INFO, moderation_message)

reject_objects.short_description = _("Reject selected moderated objects")


def set_objects_as_pending(modeladmin, request, queryset):
    for obj in queryset:
        obj.set_as_pending(moderated_by=request.user)
#    queryset.update(moderation_status=MODERATION_STATUS_PENDING)

set_objects_as_pending.short_description = _("Set selected moderated objects "\
                                           "as Pending")


class ModerationAdmin(admin.ModelAdmin):
    admin_integration_enabled = True

    def queryset(self, request):
        return self.model.unmoderated_objects.all()
        
    def get_form(self, request, obj=None):
        defaults = {}
        if obj and self.admin_integration_enabled:
            form = self.get_moderated_object_form(obj.__class__)
            defaults.update({
                'form': form
            })
        defaults.update(kwargs)
        
        superform = super(ModerationAdmin, self).get_form(request, obj)
        if not self.admin_integration_enabled:
            return superform

        if 'history' in request.path_info.split('/')[-3]:
            #HACK: check URL to determine if django-reversion is used
            #Using django-reversion
            return superform

        return make_moderatedform_from_modelform(superform, obj)

    def change_view(self, request, object_id, extra_context=None):
        if self.admin_integration_enabled:
            self.send_message(request, object_id)

        return super(ModerationAdmin, self).change_view(request, object_id, extra_context=extra_context)

    def send_message(self, request, object_id):
        try:
            obj = self.model.unmoderated_objects.get(pk=object_id)
            moderated_obj = ModeratedObject.objects.get_for_instance(obj)
            moderator = moderated_obj.moderator
            msg = self.get_moderation_message(moderated_obj.moderation_status,
                                              moderated_obj.moderation_reason,
                                              moderator.visible_until_rejected)
        except ModeratedObject.DoesNotExist:
            msg = self.get_moderation_message()

        self.message_user(request, msg)

    def save_model(self, request, obj, form, change):
        obj.save()
        automoderate(obj, request.user)

    def get_moderation_message(self, moderation_status=None, reason=None,
                               visible_until_rejected=False):
        if moderation_status == MODERATION_STATUS_PENDING:
            if visible_until_rejected:
                return _(u"Object is viewable on site, "\
                         "it will be removed if moderator rejects it")
            else:
                return _(u"Object is not viewable on site, "\
                         "it will be visible if moderator accepts it")
        elif moderation_status == MODERATION_STATUS_REJECTED:
            return _(u"Object has been rejected by moderator, "\
                    "reason: %s") % reason
        elif moderation_status == MODERATION_STATUS_APPROVED:
            return _(u"Object has been approved by moderator "\
                     "and is visible on site")
        elif moderation_status is None:
            return _("This object is not registered with "\
                     "the moderation system.")

from moderation.filterspecs import RegisteredContentTypeListFilter

class RestrictedStatusFilterSpec(ChoicesFilterSpec):
    def __init__(self, f, request, params, model, model_admin, field_path=None):
        super(RestrictedStatusFilterSpec, self).__init__(f, request, params, model, model_admin, field_path)
        self.lookup_kwarg = "moderation_status__exact"
        self.lookup_val = request.GET.get(self.lookup_kwarg)
        self.lookup_choices = (
            (_("Pending"), MODERATION_STATUS_PENDING),
            (_("Rejected"), MODERATION_STATUS_REJECTED),
        )

    def choices(self, cl):
        yield { 'selected': self.lookup_val is None,
                'query_string': cl.get_query_string({}, [self.lookup_kwarg]),
                'display': _('All') }
        for val in self.lookup_choices:
            yield { 'selected' : str(val[1]) == self.lookup_val,
                    'query_string': cl.get_query_string({self.lookup_kwarg: val[1]}),
                    'display': val[0] }

    def title(self):
        return _("moderation status")

FilterSpec.filter_specs.insert(0, (lambda f: getattr(f, 'restricted_status_filter', False), RestrictedStatusFilterSpec))


class ModeratedObjectAdmin(admin.ModelAdmin):
    date_hierarchy = 'date_created'
    list_display = ('content_object', 'content_type', 'date_created',
                    'moderation_status', 'moderated_by', 'moderation_date')
    list_filter = [('content_type', RegisteredContentTypeListFilter), 'moderation_status']
    change_form_template = 'moderation/moderate_object.html'
    change_list_template = 'moderation/moderated_objects_list.html'
    actions = [reject_objects, approve_objects]
    fieldsets = (
        (_('Object moderation'), {'fields': ('moderation_reason',)}),
        )

    def get_actions(self, request):
        actions = super(ModeratedObjectAdmin, self).get_actions(request)
        # Remove the delete_selected action if it exists
        try:
            del actions['delete_selected']
        except KeyError:
            pass
        return actions

    def content_object(self, obj):
        return unicode(obj.changed_object)

    def queryset(self, request):
        qs = super(ModeratedObjectAdmin, self).queryset(request)
        qs = qs.exclude(moderation_status=MODERATION_STATUS_APPROVED)

        return qs.exclude(moderation_state=MODERATION_DRAFT_STATE)

    def change_view(self, request, object_id, extra_context=None):
        from moderation import moderation

        moderated_object = ModeratedObject.objects.get(pk=object_id)

        changed_obj = moderated_object.changed_object

        moderator = moderation.get_moderator(changed_obj.__class__)

        if moderator.visible_until_rejected:
            old_object = changed_obj
            new_object = moderated_object.get_object_for_this_type()
        else:
            old_object = moderated_object.get_object_for_this_type()
            new_object = changed_obj

        changes = get_changes_between_models(
            old_object,
            new_object,
            moderator.fields_exclude).values()
        if request.POST:
            admin_form = self.get_form(request, moderated_object)(request.POST)

            if admin_form.is_valid():
                reason = admin_form.cleaned_data['moderation_reason']
                if 'approve' in request.POST:
                    moderation_message = moderated_object.approve(request.user, reason)
                    messages.add_message(request, messages.INFO, moderation_message)
                    return self.response_change(request, new_object)
                elif 'reject' in request.POST:
                    moderation_message = moderated_object.reject(request.user, reason)
                    messages.add_message(request, messages.INFO, moderation_message)

        content_type = ContentType.objects.get_for_model(changed_obj.__class__)
        try:
            object_admin_url = urlresolvers.reverse("admin:%s_%s_change" %
                                                    (content_type.app_label,
                                                     content_type.model),
                                                    args=(changed_obj.pk,))
        except urlresolvers.NoReverseMatch:
            object_admin_url = None

        extra_context = {'changes': changes,
                         'old_object': old_object,
                         'new_object': new_object,
                         'django_version': django.get_version()[:3],
                         'object_admin_url': object_admin_url}
        return super(ModeratedObjectAdmin, self).change_view(request,
                                                             object_id,
                                                             extra_context=extra_context)

    def response_change(self, request, obj):
        """
        Determines the HttpResponse for the change_view stage.
        """

        # Figure out where to redirect. If the user has change permission,
        # redirect to the change-list page for this object. Otherwise,
        # redirect to the admin index.
        if self.has_change_permission(request, None):
            return HttpResponseRedirect('../')
        else:
            return HttpResponseRedirect('../../../')


admin.site.register(ModeratedObject, ModeratedObjectAdmin)
