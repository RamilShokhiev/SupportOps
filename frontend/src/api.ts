export async function api<T>(path:string, options:RequestInit = {}):Promise<T> {
  const form = options.body instanceof FormData;
  const response = await fetch(`/api${path}`, { ...options, credentials:'include', headers:{...(!form ? {'Content-Type':'application/json'} : {}), ...options.headers} });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item:{msg:string}) => item.msg).join('; ') : body?.message || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return response.status === 204 ? undefined as T : response.json();
}
export const post = <T,>(path:string, body:unknown = {}) => api<T>(path,{method:'POST',body:JSON.stringify(body)});
