"""Presentation context regression in isolated SQLite; MySQL/delivery blocked."""
import json
import re
import unittest

from approval_test_support import LinkedSupplierCase
from app.models.purchase import STATUS_LABELS


class SupplierPurchasesContextTests(LinkedSupplierCase):
    def test_page_uses_all_domain_labels_without_changing_detail_payload(self):
        for role in ("admin", "asistente"):
            with self.subTest(role=role):
                client = self.clients[role]
                path = f"/api/suppliers/{self.supplier.id}"
                before = client.get(path).get_json()
                response = client.get("/suppliers")
                self.assertEqual(response.status_code, 200)
                match = re.search(r'<script[^>]*id="sp-context"[^>]*>(.*?)</script>',
                                  response.get_data(as_text=True), re.DOTALL)
                context = json.loads(match.group(1))
                self.assertEqual(context["purchaseStatusLabels"], STATUS_LABELS)
                self.assertEqual(len(context["purchaseStatusLabels"]), 11)
                self.assertEqual(client.get(path).get_json(), before)
                self.assertEqual(self.stock(), 10)
                self.assertNotIn("currency", before["supplied_products"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
