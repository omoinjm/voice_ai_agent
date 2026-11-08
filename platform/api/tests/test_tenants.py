import pytest

from app.routers.tenants import slugify

from .conftest import as_user


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


async def test_requires_sign_in(client):
    assert (await client.get("/me")).status_code == 401
    assert (await client.get("/me", headers={"Authorization": "Bearer nonsense"})).status_code == 401


async def test_me_creates_user_once(client):
    a = (await client.get("/me", headers=as_user("Owner@Example.com"))).json()
    b = (await client.get("/me", headers=as_user("owner@example.com"))).json()
    assert a["id"] == b["id"] and a["email"] == "owner@example.com" and a["is_admin"] is False


async def test_create_tenant_makes_owner_and_draft_application(client):
    r = await client.post(
        "/tenants", json={"name": "Dr Smith's Practice", "category": "medical"}, headers=as_user("smith@example.com")
    )
    assert r.status_code == 201, r.text
    tenant = r.json()
    assert tenant["slug"] == "dr-smith-s-practice" and tenant["status"] == "registered"

    mine = (await client.get("/tenants", headers=as_user("smith@example.com"))).json()
    assert [t["id"] for t in mine] == [tenant["id"]]


async def test_duplicate_names_get_distinct_slugs(client):
    h = as_user("a@example.com")
    s1 = (await client.post("/tenants", json={"name": "Acme Law", "category": "legal"}, headers=h)).json()["slug"]
    s2 = (await client.post("/tenants", json={"name": "Acme Law", "category": "legal"}, headers=h)).json()["slug"]
    assert s1 == "acme-law" and s2.startswith("acme-law-") and s1 != s2


async def test_rejects_unknown_category(client):
    r = await client.post("/tenants", json={"name": "X Corp", "category": "casino"}, headers=as_user("x@example.com"))
    assert r.status_code == 422


async def test_tenants_are_private_but_admins_see_all(client):
    body = {"name": "Cover Insure", "category": "insurance"}
    t = (await client.post("/tenants", json=body, headers=as_user("o@ex.com"))).json()

    stranger = as_user("stranger@ex.com")
    assert (await client.get(f"/tenants/{t['id']}", headers=stranger)).status_code == 404
    assert (await client.get("/tenants", headers=stranger)).json() == []

    admin = as_user("ops@ex.com", admin=True)
    assert (await client.get(f"/tenants/{t['id']}", headers=admin)).status_code == 200
    assert len((await client.get("/tenants", headers=admin)).json()) == 1


@pytest.mark.parametrize(
    ("name", "slug"),
    [("Acme", "acme"), ("123 Dental", "b-123-dental"), ("Q", "q-biz"), ("  !!Weird & Co!! ", "weird-co")],
)
def test_slugify(name, slug):
    assert slugify(name) == slug
