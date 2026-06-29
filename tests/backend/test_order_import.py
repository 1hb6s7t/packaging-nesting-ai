from fastapi.testclient import TestClient

from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def test_csv_order_import_file() -> None:
    csv_bytes = (
        "order_id,product_name,category,is_repeat_order,quote_amount,contacted,quantity,material,thickness,allowed_rotations\n"
        "CSV001,彩盒彩盒,box,true,6800,true,1200,white_card,350gsm,\"0,90,180,270\"\n"
    ).encode("utf-8")
    response = client.post(
        "/api/orders/import-file",
        files={"file": ("orders.csv", csv_bytes, "text/csv")},
        headers=auth_headers(client),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["imported_count"] == 1
    assert payload["orders"][0]["order_id"] == "CSV001"
    assert payload["orders"][0]["allowed_rotations"] == [0, 90, 180, 270]
