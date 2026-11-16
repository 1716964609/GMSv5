import cf from 'cloudfront';

const kvs = cf.kvs();

function deny(code) {
  return {
    statusCode: code,
    headers: {
      'content-type': { value: 'text/plain' }
    },
    body: 'Request rejected'
  };
}

async function handler(event) {
  const request = event.request;

  if (
    request.method !== 'POST' ||
    request.uri !== '/notify' ||
    Object.keys(request.querystring || {}).length !== 0
  ) {
    return deny(403);
  }

  const supplied = request.headers['x-gms-notify-token'];
  if (!supplied) return deny(403);

  let expected;
  let webhookPath;

  try {
    expected = await kvs.get('relay-token');
    webhookPath = await kvs.get('discord-webhook-path');
  } catch (error) {
    return deny(503);
  }

  if (supplied.value !== expected) return deny(403);

  if (
    !/^\/api\/webhooks\/[0-9]+\/[A-Za-z0-9_-]+$/.test(
      webhookPath
    )
  ) {
    return deny(503);
  }

  delete request.headers['x-gms-notify-token'];
  request.uri = webhookPath;

  return request;
}
