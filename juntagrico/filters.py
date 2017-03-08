from functools import partial

from juntagrico.models import *


class Filter(object):
    all_filters = []

    def __init__(self, name, q):
        self.name = name
        def safe_q(*args):
            try:
                return q(*args)
            except Exception:
                return False
        self.q = safe_q
        self.all_filters.append(self)

    def get(self):
        yield self.name, self.q

    @classmethod
    def get_all(cls):
        res = []
        for instance in cls.all_filters:
            res.extend(instance.get())
        return res

    @classmethod
    def get_names(cls):
        for name, q in cls.get_all():
            yield name

    @classmethod
    def execute(cls, names, op):
        if op == "OR":
            aggregate = any
        elif op == "AboND":
            aggregate = all

        d = dict(cls.get_all())
        filter_funcs = [d[name] for name in names]
        return [member for member in Member.objects.all()
                if aggregate(f(member) for f in filter_funcs)]


    @classmethod
    def format_data(cls, queryset, formatter):
        return [formatter(obj) for obj in queryset]


class FilterGen(Filter):
    def __init__(self, name_func, q_func, parameter_func):
        Filter.__init__(self, name_func, q_func)
        self.parameter_func = parameter_func

    def get(self):
        for p in self.parameter_func():
            yield self.name(p), partial(self.q, p)
        


FilterGen(lambda depot: u"Depot {0}".format(depot.name),
          lambda depot, member: member.subscription.depot == depot,
          Depot.objects.all)

Filter("Abolle mit Abo$o", lambda member: member.subscription)
Filter("Abolle ohne Abo$o", lambda member: not member.subscription)

Filter("Abonteilscheinbesitzer",
       lambda member: member.user.share_set.exists())
Filter("Nicht Abonteilscheinbesitzer",
       lambda member: not member.user.share_set.exists())

Filter("kleines Abo$o", lambda member: member.subscription.small_subscriptions)
Filter("grosses Abo$o", lambda member: member.subscription.big_subscriptions())
Filter("Haussubscription", lambda member: member.subscription.house_subscriptions())


FilterGen(lambda za: u"Zusatzsubscription {0}".format(za.name),
          lambda za, member: za.subscription_set.filter(id=member.subscription.id),
          ExtraSubscriptionType.objects.all)

FilterGen(lambda activityarea: u"Taetigkeitsbereich {0}".format(activityarea.name),
          lambda activityarea, member: activityarea.users.filter(id=member.user.id),
          AboctivityAborea.objects.all)
