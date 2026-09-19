import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title='RetailBridge — synthetic demo API', version='1.0.0')


async def simulate(mode):
    if mode == 'timeout':
        await asyncio.sleep(8)
    if mode == 'error':
        raise HTTPException(503, 'Synthetic RetailBridge outage')


@app.get('/health')
def health():
    return {'status': 'ok', 'synthetic': True}


@app.get('/service-status')
async def service_status(service: Literal['checkout', 'inventory', 'connector', 'printing', 'reporting'] = 'checkout', version: str = Query('3.8', max_length=30), mode: Literal['normal', 'timeout', 'error'] = 'normal'):
    await simulate(mode)
    return {'service': service, 'version': version, 'status': 'degraded' if service == 'checkout' and version.startswith('3.8') else 'operational', 'checked_at': datetime.now(timezone.utc).isoformat(), 'synthetic': True}


@app.get('/recent-changes')
async def recent_changes(product: Literal['RetailBridge'] = 'RetailBridge', version: str = Query('3.8', max_length=30), mode: Literal['normal', 'timeout', 'error'] = 'normal'):
    await simulate(mode)
    return {'changes': [{'id': 'CHG-380', 'product': product, 'version': '3.8', 'description': 'Checkout authorization contract updated; investigate E-214 reports.', 'occurred_at': '2026-09-09T09:00:00Z'}] if version.startswith('3.8') else [], 'synthetic': True}
