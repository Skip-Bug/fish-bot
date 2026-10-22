diff --git a/strapi_client.py b/strapi_client.py
index 9d808f5..3012110 100644
--- a/strapi_client.py
+++ b/strapi_client.py
@@ -13,12 +13,12 @@ def get_products(strapi_url, session, redis_connect=None, offset=0, limit=None):
     if limit is None:
         limit = PRODUCTS_PER_PAGE
 
-    if redis_connect:
-        cached = redis_connect.get(PRODUCTS_CACHE_KEY)
-        if cached:
-            cached_data = json.loads(cached)
-            if isinstance(cached_data, list):
-                return cached_data[offset : offset + limit], len(cached_data)
+    cached_raw = redis_connect.get(PRODUCTS_CACHE_KEY) if redis_connect else None
+
+    cached_data = json.loads(cached_raw) if cached_raw else None
+
+    if isinstance(cached_data, list):
+        return cached_data[offset : offset + limit], len(cached_data)
 
     url = f"{strapi_url}/api/products"
     params = {
@@ -27,10 +27,17 @@ def get_products(strapi_url, session, redis_connect=None, offset=0, limit=None):
     }
     response = session.get(url, params=params, timeout=5)
     response.raise_for_status()
-    data = response.json()
+    api_response = response.json()
 
-    all_products = data.get("data", [])
-    total = data.get("meta", {}).get("pagination", {}).get("total", len(all_products))
+    all_products = api_response.get("data", [])
+    total = (
+        api_response.get(
+            "meta",
+            {},
+        )
+        .get("pagination", {})
+        .get("total", len(all_products))
+    )
 
     if redis_connect:
         redis_connect.setex(
