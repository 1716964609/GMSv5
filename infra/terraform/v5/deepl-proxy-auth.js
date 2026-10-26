import cf from 'cloudfront';

const kvs = cf.kvs();

function deny(statusCode) {
  return {
    statusCode,
    headers: {
      'content-type': { value: 'text/plain' }
    },
    body: 'Request rejected'
  };
}

async function handler(event) {
  const request = event.request;

  // Only allow DeepL translation requests
  const translation =
    request.method === 'POST' &&
    request.uri === '/v2/translate';

  const createGlossary =
    request.method === 'POST' &&
    request.uri === '/v2/glossaries';

  const deleteGlossary =
    request.method === 'DELETE' &&
    /^\/v2\/glossaries\/[A-Za-z0-9-]+$/.test(request.uri);

  if (!(translation || createGlossary || deleteGlossary)) {
    return deny(403);
  }

  const token = request.headers['x-gms-proxy-token'];

  if (!token) {
    return deny(403);
  }

  let expected;
  try {
    expected = await kvs.get('shared-token');
  } catch (error) {
    return deny(503);
  }

  if (token.value !== expected) {
    return deny(403);
  }

  // Never forward the proxy token to DeepL
  delete request.headers['x-gms-proxy-token'];

  return request;
}
