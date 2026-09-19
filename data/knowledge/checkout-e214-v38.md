# E-214: checkout authorization contract mismatch (3.8)

> SYNTHETIC DEMO DOCUMENT. RetailBridge, organizations and procedures are fictional.

Product: RetailBridge | versions: 3.8–3.8 | document revision: 1 | organization: both

E-214 in RetailBridge 3.8 means that the checkout authorization contract does not match the connector contract after an upgrade. Symptoms: payment cannot complete, checkout blocked, касса не завершает оплату, ödeme tamamlanmıyor. This is a diagnostic hypothesis until logs confirm it. Collect store IDs, timestamps, connector build and correlation IDs; read checkout service status and recent changes. A degraded service plus the matching release is sufficient for an engineering escalation, not proof of root cause. Ask the support operator to review an engineering issue. Do not retry or reverse a customer payment automatically. Never apply the 3.7 cache-refresh procedure to 3.8.
