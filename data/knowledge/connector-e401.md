# E-401: connector token expired

> SYNTHETIC DEMO DOCUMENT. RetailBridge, organizations and procedures are fictional.

Product: RetailBridge | versions: 3.7–3.9 | document revision: 1 | organization: both

RetailBridge E-401 means the connector authentication token has expired. Connector login failure, токен истёк, bağlantı belirteci süresi doldu. Verify the expiry timestamp in the connector administration screen. A tenant administrator can rotate the credential through the approved secret manager and reconnect. Never ask a customer to paste credentials, tokens or passwords into a ticket. Repeated E-401 after rotation needs an investigation of expiry and clock settings. Do not claim a service outage from this error alone.
