# Append this mount fragment to the exact reviewed guarded_idle_webhook.py.
# Windmill dependency discovery must see the SDK at module scope.
import wmill


def main():
    return bind_webhook(bind_foo_transport(wmill.Windmill()))()
