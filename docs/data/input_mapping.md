# Input mapping

Real data is not bundled or published. An explicit JSON mapping should name
source files and columns rather than relying on guessed positions:

```json
{
  "customers": {"file": "customers.csv", "customer_id": "client_id", "segment": "segment"},
  "products": {"file": "products.xlsx", "product_id": "sku", "name": "name"},
  "transactions": {"file": "sales.csv", "transaction_id": "line_id", "customer_id": "client_id",
    "product_id": "sku", "purchased_at": "created_at", "quantity": "quantity"}
}
```

Missing or ambiguous required columns must produce an actionable error. IDs are
read as strings to preserve leading zeroes. The current implementation exposes
the synthetic path and intentionally refuses real mode until this mapping is
implemented against an available, authorized source schema.

