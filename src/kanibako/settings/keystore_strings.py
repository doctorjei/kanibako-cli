"""The ``%``-format error-message templates :mod:`keystore` raises."""

ERR_RESERVEDKEY_DUNDER = ("key %s is reserved: dunder names (__x__) are the store's attribute "
                          "space, not key space.")
ERR_RESERVEDKEY_SHADOW = ("key %s is reserved: it would shadow a real attribute on the settings "
                          "store. Reserved names: %s")

ERR_TYPE_NONSTRING_KEY = "KeyStore keys must be str, got %s: %s"
ERR_TYPE_KEYSTORE_ARGS = "KeyStore expected at most 1 positional argument, got %s"
ERR_ATTRIBUTE_NO_KEY   = "%s object has no key %s"


