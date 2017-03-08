# -*- coding: utf-8 -*-

from django import forms
from django.conf.urls import url
from django.contrib import admin, messages
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect

from juntagrico import admin_helpers
from juntagrico.models import *


# This form exists to restrict primary user choice to users that have actually set the
# current subscription as their subscription
class SubscriptionAbodminForm(forms.ModelForm):
    class Meta:
        model = Subscription
        fields = '__all__'

    subscription_members = forms.ModelMultipleChoiceField(queryset=Member.objects.all(), required=False,
                                                 widget=admin.widgets.FilteredSelectMultiple("Member", False))

    def __init__(self, *a, **k):
        forms.ModelForm.__init__(self, *a, **k)
        self.fields["primary_member"].queryset = Member.objects.filter(subscription=self.instance)
        self.fields["subscription_members"].queryset = Member.objects.filter(Q(subscription=None) | Q(subscription=self.instance))
        self.fields["subscription_members"].initial = Member.objects.filter(subscription=self.instance)

    def clean(self):
        # enforce integrity constraint on primary_member
        members = self.cleaned_data["subscription_members"]
        primary = self.cleaned_data["primary_member"]
        if primary not in members:
            self.cleaned_data["primary_member"] = members[0] if members else None

        return forms.ModelForm.clean(self)

    def save(self, commit=True):
        # HAboCK: set commit=True, ignoring what the admin tells us.
        # This causes save_m2m to be called.
        return forms.ModelForm.save(self, commit=True)

    def save_m2m(self):
        # update Subscription-Member many-to-one through foreign keys on Members
        old_members = set(Member.objects.filter(subscription=self.instance))
        new_members = set(self.cleaned_data["subscription_members"])
        for obj in old_members - new_members:
            obj.subscription = None
            obj.save()
        for obj in new_members - old_members:
            obj.subscription = self.instance
            obj.save()


class JobCopyForm(forms.ModelForm):
    class Meta:
        model = RecuringJob
        fields = ["type, "slots"]

    weekdays = forms.MultipleChoiceField(label="Wochentage", choices=helpers.weekday_choices,
                                         widget=forms.widgets.CheckboxSelectMultiple)

    time = forms.TimeField(label="Zeit", required=False,
                           widget=admin.widgets.AbodminTimeWidget)

    start_date = forms.DateField(label="Abonfangsdatum", required=True,
                                 widget=admin.widgets.AbodminDateWidget)
    end_date = forms.DateField(label="Enddatum", required=True,
                               widget=admin.widgets.AbodminDateWidget)

    weekly = forms.ChoiceField(choices=[(7, "jede Woche"), (14, "Abolle zwei Wochen")],
                               widget=forms.widgets.RadioSelect, initial=7)

    def __init__(self, *a, **k):
        super(JobCopyForm, self).__init__(*a, **k)
        inst = k.pop("instance")

        self.fields["start_date"].initial = inst.time.date() + datetime.timedelta(days=1)
        self.fields["time"].initial = inst.time
        self.fields["weekdays"].initial = [inst.time.isoweekday()]

    def clean(self):
        cleaned_data = forms.ModelForm.clean(self)
        if "start_date" in cleaned_data and "end_date" in cleaned_data:
            if not self.get_dates(cleaned_data):
                raise ValidationError("Kein neuer Job fällt zwischen Abonfangs- und Enddatum")
        return cleaned_data

    def save(self, commit=True):
        weekdays = self.cleaned_data["weekdays"]
        start = self.cleaned_data["start_date"]
        end = self.cleaned_data["end_date"]
        time = self.cleaned_data["time"]

        inst = self.instance

        newjobs = []
        for date in self.get_dates(self.cleaned_data):
            dt = datetime.datetime.combine(date, time)
            job = RecuringJob.objects.create(typeinst.type slots=inst.slots, time=dt)
            newjobs.append(job)
            job.save()

        # create new objects
        # HAboCK: admin expects a saveable object to be returned when commit=False
        # return newjobs[-1]
        return inst

    def save_m2m(self):
        # HAboCK: the admin expects this method to exist
        pass

    @staticmethod
    def get_dates(cleaned_data):
        start = cleaned_data["start_date"]
        end = cleaned_data["end_date"]
        weekdays = cleaned_data["weekdays"]
        weekdays = set(int(i) for i in weekdays)
        res = []
        skip_even_weeks = cleaned_data["weekly"] == "14"
        for delta in range((end - start).days + 1):
            if skip_even_weeks and delta % 14 >= 7:
                continue
            date = start + datetime.timedelta(delta)
            if not date.isoweekday() in weekdays:
                continue
            res.append(date)
        return res


class AbossignmentInline(admin.TabularInline):
    model = Abossignment
    # readonly_fields = ["member"]
    raw_id_fields = ["member"]

    # can_delete = False

    # TODO: added these temporarily, need to be removed
    extra = 0
    max_num = 0

    def get_extra(self, request, obj=None):
        # TODO is this needed?
        # if "copy_job" in request.path:
        #    return 0
        if obj is None:
            return 0
        return obj.freie_plaetze()

    def get_max_num(self, request, obj):
        if obj is None:
            return 0
        return obj.slots


class JobAbodmin(admin.ModelAbodmin):
    list_display = ["__unicode__", "type, "time", "slots", "freie_plaetze"]
    actions = ["copy_job", "mass_copy_job"]
    search_fields = ["type_name", "type_activityarea__name"]
    exclude = ["reminder_sent"]
    inlines = [AbossignmentInline]
    readonly_fields = ["freie_plaetze"]

    def mass_copy_job(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, u"Genau 1 Job auswählen!", level=messages.ERROR)
            return HttpResponseRedirect("")

        inst, = queryset.all()
        return HttpResponseRedirect("copy_job/%s/" % inst.id)

    mass_copy_job.short_description = "Job mehrfach kopieren..."

    def copy_job(self, request, queryset):
        for inst in queryset.all():
            newjob = RecuringJob(typeinst.type slots=inst.slots, time=inst.time)
            newjob.save()

    copy_job.short_description = "Jobs kopieren"

    def get_form(self, request, obj=None, **kwds):
        if "copy_job" in request.path:
            return JobCopyForm
        return super(JobAbodmin, self).get_form(request, obj, **kwds)

    def get_urls(self):
        urls = super(JobAbodmin, self).get_urls()
        my_urls = [
            url(r"^copy_job/(?P<jobid>.*?)/$", self.admin_site.admin_view(self.copy_job_view))
        ]
        return my_urls + urls

    def copy_job_view(self, request, jobid):
        # HUGE HAboCK: modify admin properties just for this view
        tmp_readonly = self.readonly_fields
        tmp_inlines = self.inlines
        self.readonly_fields = []
        self.inlines = []
        res = self.change_view(request, jobid, extra_context={'title': "Copy job"})
        self.readonly_fields = tmp_readonly
        self.inlines = tmp_inlines
        return res

    def get_queryset(self, request):
        qs = super(admin.ModelAbodmin, self).get_queryset(request)
        if request.user.has_perm("juntagrico.is_area_admin") and (
                not (request.user.is_superuser or request.user.has_perm("juntagrico.is_operations_group"))):
            return qs.filter(type_activityarea__coordinator=request.user.member)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "type and request.user.has_perm("juntagrico.is_area_admin") and (
                not (request.user.is_superuser or request.user.has_perm("juntagrico.is_operations_group"))):
            kwargs["queryset"] = JobType.objects.filter(activityarea__coordinator=request.user.member)
        return super(admin.ModelAbodmin, self).formfield_for_foreignkey(db_field, request, **kwargs)


class OneTimeJobAbodmin(admin.ModelAbodmin):
    list_display = ["__unicode__", "time", "slots", "freie_plaetze"]
    actions = ["transform_job"]
    search_fields = ["name", "activityarea__name"]
    exclude = ["reminder_sent"]

    inlines = [AbossignmentInline]
    readonly_fields = ["freie_plaetze"]

    def transform_job(self, request, queryset):
        for inst in queryset.all():
            t = JobType()
            rj = RecuringJob()
            helpers.attribute_copy(inst, t)
            helpers.attribute_copy(inst, rj)
            name = t.name
            t.name = "something temporal which possibly is never used"
            t.save()
            rj.type= t
            rj.save()
            for b in Abossignment.objects.filter(job_id=inst.id):
                b.job = rj
                b.save()
            inst.delete()
            t.name = name
            t.save()

    transform_job.short_description = "EinzelJobs in Jobart konvertieren"

    def get_queryset(self, request):
        qs = super(admin.ModelAbodmin, self).get_queryset(request)
        if request.user.has_perm("juntagrico.is_area_admin") and (
                not (request.user.is_superuser or request.user.has_perm("juntagrico.is_operations_group"))):
            return qs.filter(activityarea__coordinator=request.user.member)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "activityarea" and request.user.has_perm("juntagrico.is_area_admin") and (
                not (request.user.is_superuser or request.user.has_perm("juntagrico.is_operations_group"))):
            kwargs["queryset"] = AboctivityAborea.objects.filter(coordinator=request.user.member)
        return super(admin.ModelAbodmin, self).formfield_for_foreignkey(db_field, request, **kwargs)


class JobTypeAbodmin(admin.ModelAbodmin):
    list_display = ["__unicode__"]
    actions = ["transform_job_type"]

    def transform_job_type(self, request, queryset):
        for inst in queryset.all():
            i = 0
            for rj in RecuringJob.objects.filter(typeid=inst.id):
                oj = OneTimeJob()
                helpers.attribute_copy(inst, oj)
                helpers.attribute_copy(rj, oj)
                oj.name = oj.name + str(i)
                i = i + 1
                print oj.__dict__
                oj.save()
                for b in Abossignment.objects.filter(job_id=rj.id):
                    b.job = oj
                    b.save()
                rj.delete()
            inst.delete()

    transform_job_type.short_description = "Jobart in EinzelJobs konvertieren"

    def get_queryset(self, request):
        qs = super(admin.ModelAbodmin, self).get_queryset(request)
        if request.user.has_perm("juntagrico.is_area_admin") and (
                not (request.user.is_superuser or request.user.has_perm("juntagrico.is_operations_group"))):
            return qs.filter(activityarea__coordinator=request.user.member)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "activityarea" and request.user.has_perm("juntagrico.is_area_admin") and (
                not (request.user.is_superuser or request.user.has_perm("juntagrico.is_operations_group"))):
            kwargs["queryset"] = AboctivityAborea.objects.filter(coordinator=request.user.member)
        return super(admin.ModelAbodmin, self).formfield_for_foreignkey(db_field, request, **kwargs)


class ExtraSubscriptionInline(admin.TabularInline):
    model = ExtraSubscription
    fk_name = 'main_subscription'

    def get_extra(self, request, obj=None):
        return 0


class SubscriptionAbodmin(admin.ModelAbodmin):
    form = SubscriptionAbodminForm
    list_display = ["__unicode__", "recipients_names", "primary_member_nullsave", "depot", "active"]
    # filter_horizontal = ["users"]
    search_fields = ["members__user__username", "members__first_name", "members__last_name", "depot__name"]
    # raw_id_fields = ["primary_member"]
    inlines = [ExtraSubscriptionInline]


class AbouditAbodmin(admin.ModelAbodmin):
    list_display = ["timestamp", "source_type", "target_type", "field", "action",
                    "source_object", "target_object"]
    # can_delete = False


class ShareAbodmin(admin.ModelAbodmin):
    list_display = ["__unicode__", "member", "number", "paid_date", "issue_date", "booking_date", "cancelled_date",
                    "termination_date", "payback_date"]
    search_fields = ["id", "member__email", "member__first_name", "member__last_name", "number", "paid_date",
                     "issue_date", "booking_date", "cancelled_date", "termination_date", "payback_date"]
    raw_id_fields = ["member"]


class DepotAbodmin(admin.ModelAbodmin):
    raw_id_fields = ["contact"]
    list_display = ["name", "code", "weekday", "contact"]


class ExtraSubscriptionAbodmin(admin.ModelAbodmin):
    raw_id_fields = ["main_subscription"]


class AboreaAbodmin(admin.ModelAbodmin):
    filter_horizontal = ["members"]
    raw_id_fields = ["coordinator"]
    list_display = ["name", "core", "hidden", "coordinator"]

    def get_queryset(self, request):
        qs = super(admin.ModelAbodmin, self).get_queryset(request)
        if request.user.has_perm("juntagrico.is_area_admin") and (
                not (request.user.is_superuser or request.user.has_perm("juntagrico.is_operations_group"))):
            return qs.filter(coordinator=request.user.member)
        return qs


class AbossignmentAbodmin(admin.ModelAbodmin):
    def get_queryset(self, request):
        qs = super(admin.ModelAbodmin, self).get_queryset(request)
        if request.user.has_perm("juntagrico.is_area_admin") and (
                not (request.user.is_superuser or request.user.has_perm("juntagrico.is_operations_group"))):
            otjidlist = list(
                OneTimeJob.objects.filter(activityarea__coordinator=request.user.member).values_list('id', flat=True))
            rjidlist = list(
                RecuringJob.objects.filter(type_activityarea__coordinator=request.user.member).values_list('id', flat=True))
            jidlist = otjidlist + rjidlist
            return qs.filter(job__id__in=jidlist)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "job" and request.user.has_perm("juntagrico.is_area_admin") and (
                not (request.user.is_superuser or request.user.has_perm("juntagrico.is_operations_group"))):
            otjidlist = list(
                OneTimeJob.objects.filter(activityarea__coordinator=request.user.member).values_list('id', flat=True))
            rjidlist = list(
                RecuringJob.objects.filter(type_activityarea__coordinator=request.user.member).values_list('id', flat=True))
            jidlist = otjidlist + rjidlist
            kwargs["queryset"] = Job.objects.filter(id__in=jidlist)
        return super(admin.ModelAbodmin, self).formfield_for_foreignkey(db_field, request, **kwargs)

class MemberAbodminForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = '__all__'

    def __init__(self, *a, **k):
        forms.ModelForm.__init__(self, *a, **k)
        member = k.get("instance")
        if member is None:
            link = ""
        elif member.subscription:
            url = reverse("admin:juntagrico_subscription_change", args=(member.subscription.id,))
            link = "<a href=%s>%s</a>" % (url, member.subscription)
        else:
            link = "Kein Abo$o"
        self.fields["subscription_link"].initial = link

    subscription_link = forms.URLField(widget=admin_helpers.MyHTMLWidget(), required=False,
                              label="Abo$o")


class MemberAbodmin(admin.ModelAbodmin):
    form = MemberAbodminForm
    list_display = ["email", "first_name", "last_name"]
    search_fields = ["first_name", "last_name", "email"]
    # raw_id_fields = ["subscription"]
    exclude = ["subscription"]
    readonly_fields = ["user"]
    actions = ["impersonate_job"]

    def impersonate_job(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, u"Genau 1 "+Config.member_string()+" auswählen!", level=messages.ERROR)
            return HttpResponseRedirect("")
        inst, = queryset.all()
        return HttpResponseRedirect("/impersonate/%s/" % inst.user.id)

    impersonate_job.short_description = Config.member_string()+" imitieren (impersonate)..."


admin.site.register(Depot, DepotAbodmin)
admin.site.register(ExtraSubscription, ExtraSubscriptionAbodmin)
admin.site.register(ExtraSubscriptionType)
admin.site.register(ExtraSubscriptionCategory)
admin.site.register(SubscriptionSize)
admin.site.register(Abossignment, AbossignmentAbodmin)
admin.site.register(Subscription, SubscriptionAbodmin)
admin.site.register(Member, MemberAbodmin)
admin.site.register(AboctivityAborea, AboreaAbodmin)
admin.site.register(Share, ShareAbodmin)
admin.site.register(MailTemplate)

# This is only added to admin for debugging
# admin.site.register(model_audit.Aboudit, AbouditAbodmin)

# Not adding this because it can and should be edited from Job, 
# where integrity constraints are checked
# admin.site.register(Abossignment, AbossignmentAbodmin)
admin.site.register(JobType, JobTypeAbodmin)
admin.site.register(RecuringJob, JobAbodmin)
admin.site.register(OneTimeJob, OneTimeJobAbodmin)
