from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView, FormView
from django.views.generic.edit import ModelFormMixin

from juntagrico.dao.depotdao import DepotDao
from juntagrico.dao.memberdao import MemberDao
from juntagrico.forms import *
from juntagrico.models import *
from juntagrico.util import temporal
from juntagrico.decorators import create_subscription_session
from juntagrico.util.form_evaluation import selected_subscription_types
from juntagrico.util.management import *


@create_subscription_session
def cs_select_subscription(request, cs_session):
    if request.method == 'POST':
        # create dict with subscription type -> selected amount
        selected = selected_subscription_types(request.POST)
        cs_session.subscriptions = selected
        return redirect(cs_session.next_page())
    render_dict = {
        'selected_subscriptions': cs_session.subscriptions,
        'hours_used': Config.assignment_unit() == 'HOURS',
        'products': SubscriptionProductDao.get_all(),
    }
    return render(request, 'createsubscription/select_subscription.html', render_dict)


@create_subscription_session
def cs_select_depot(request, cs_session):
    if request.method == 'POST':
        cs_session.depot = DepotDao.depot_by_id(request.POST.get('depot'))
        return redirect(cs_session.next_page())

    depots = DepotDao.all_depots()
    requires_map = any(depot.has_geo for depot in depots)
    render_dict = {
        'member': cs_session.main_member,
        'depots': depots,
        'selected': cs_session.depot,
        'requires_map': requires_map,
    }
    return render(request, 'createsubscription/select_depot.html', render_dict)


@create_subscription_session
def cs_select_start_date(request, cs_session):
    subscription_form = SubscriptionForm(initial={
        'start_date': cs_session.start_date or temporal.start_of_next_business_year()
    })
    if request.method == 'POST':
        subscription_form = SubscriptionForm(request.POST)
        if subscription_form.is_valid():
            cs_session.start_date = subscription_form.cleaned_data['start_date']
            return redirect(cs_session.next_page())
    render_dict = {
        'start_date': temporal.start_of_next_business_year(),
        'subscriptionform': subscription_form,
    }
    return render(request, 'createsubscription/select_start_date.html', render_dict)


class CSAddMemberView(FormView, ModelFormMixin):
    template_name = 'createsubscription/add_member_cs.html'
    form_class = RegisterMemberForm

    def __init__(self):
        super().__init__()
        self.cs_session = None
        self.object = None
        self.edit = False

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            co_members=self.cs_session.co_members,
            edit_member=self.edit,
            **kwargs
        )

    def get_initial(self):
        # use address from main member as default
        mm = self.cs_session.main_member
        return {
            'addr_street': mm.addr_street,
            'addr_zipcode': mm.addr_zipcode,
            'addr_location': mm.addr_location,
        }

    @method_decorator(create_subscription_session)
    def dispatch(self, request, cs_session, *args, **kwargs):
        self.cs_session = cs_session
        self.edit = int(request.GET.get('edit', request.POST.get('edit', 0)))
        # function: edit co-member from list
        if self.edit:
            self.object = self.cs_session.get_co_member(self.edit - 1)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        member = MemberDao.member_by_email(request.POST.get('email'))
        # use existing member if not blocked
        if member is not None:
            if member.blocked:
                return self.render_to_response(self.get_context_data(member_blocked=True, **kwargs))
            return self._add_or_replace_co_member(member)
        # else: validate form
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        # create new member from form data
        return self._add_or_replace_co_member(Member(**form.cleaned_data))

    def _add_or_replace_co_member(self, member):
        if self.edit:
            self.cs_session.replace_co_member(self.edit - 1, member)
            return redirect(self.cs_session.next_page())
        else:
            self.cs_session.add_co_member(member)
            return redirect('.')

    def get(self, request, *args, **kwargs):
        # done: move to next page
        if request.GET.get('next') is not None:
            self.cs_session.co_members_done = True
            return redirect(self.cs_session.next_page())

        # function: remove co-members from list
        remove_member = int(request.GET.get('remove', 0))
        if remove_member:
            self.cs_session.remove_co_member(remove_member - 1)
            return redirect(self.cs_session.next_page())

        # render page
        return super().get(request, *args, **kwargs)


class CSSelectSharesView(TemplateView):
    template_name = 'createsubscription/select_shares.html'

    def __init__(self):
        super().__init__()
        self.cs_session = None

    def get_context_data(self, shares):
        return {
            'shares': shares,
            'member': self.cs_session.main_member,
            'co_members': self.cs_session.co_members
        }

    @method_decorator(create_subscription_session)
    def dispatch(self, request, cs_session, *args, **kwargs):
        self.cs_session = cs_session
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        # read form
        self.cs_session.main_member.new_shares = int(request.POST.get('shares_mainmember', 0))
        for co_member in self.cs_session.co_members:
            co_member.new_shares = int(request.POST.get(co_member.email, 0))
        # evaluate
        shares = self.cs_session.count_shares()
        if self.cs_session.evaluate_ordered_shares(shares):
            return redirect('cs-summary')
        # show error otherwise
        shares['error'] = True
        return super().get(request, *args, shares=shares, **kwargs)

    def get(self, request, *args, **kwargs):
        # evaluate number of ordered shares
        shares = self.cs_session.count_shares()
        return super().get(request, *args, shares=shares, **kwargs)


class CSSummaryView(TemplateView):
    template_name = 'createsubscription/summary.html'

    def get_context_data(self, cs_session, **kwargs):
        return cs_session.to_dict()

    @method_decorator(create_subscription_session)
    def dispatch(self, request, cs_session, *args, **kwargs):
        # remember that user reached summary to come back here after editing
        cs_session.edit = True
        return super().dispatch(request, *args, cs_session=cs_session, **kwargs)

    @staticmethod
    def post(request, cs_session):
        # create subscription
        subscription = None
        if sum(cs_session.subscriptions.values()) > 0:
            subscription = create_subscription(cs_session.start_date, cs_session.depot, cs_session.subscriptions)

        # create and/or add members to subscription and create their shares
        create_or_update_member(cs_session.main_member, subscription, cs_session.main_member.new_shares)
        for co_member in cs_session.co_members:
            create_or_update_member(co_member, subscription, co_member.new_shares, cs_session.main_member)

        # set primary member of subscription
        if subscription is not None:
            subscription.primary_member = cs_session.main_member
            subscription.save()
            send_subscription_created_mail(subscription)

        # finish registration
        return cs_finish(request)


@create_subscription_session
def cs_finish(request, cs_session, cancelled=False):
    if request.user.is_authenticated:
        cs_session.clear()
        return redirect('sub-detail')
    elif cancelled:
        cs_session.clear()
        return redirect('http://'+Config.server_url())
    else:
        # keep session for welcome message
        return redirect('welcome')


@create_subscription_session
def cs_welcome(request, cs_session):
    render_dict = {
        'no_subscription': cs_session.main_member.future_subscription is None
    }
    cs_session.clear()
    return render(request, 'welcome.html', render_dict)
