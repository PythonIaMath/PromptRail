const DEFAULT_INVALID_MESSAGE = "Invalid JSON request body.";
const DEFAULT_TOO_LARGE_MESSAGE = "Request body is too large.";

class BoundedJsonBodyError extends Error {
  constructor({ code, message, status }) {
    super(message);
    this.name = "BoundedJsonBodyError";
    this.code = code;
    this.status = status;
  }
}

function invalidBody() {
  return new BoundedJsonBodyError({
    code: "invalid_json_body",
    message: DEFAULT_INVALID_MESSAGE,
    status: 400,
  });
}

function bodyTooLarge() {
  return new BoundedJsonBodyError({
    code: "request_body_too_large",
    message: DEFAULT_TOO_LARGE_MESSAGE,
    status: 413,
  });
}

async function readBoundedJson(request, { maxBytes }) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0) {
    throw new TypeError("maxBytes must be a positive safe integer.");
  }

  const declaredLength = request.headers.get("content-length");
  if (/^\d+$/.test(declaredLength || "") && Number(declaredLength) > maxBytes) {
    throw bodyTooLarge();
  }
  if (!request.body) throw invalidBody();

  const reader = request.body.getReader();
  const chunks = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!(value instanceof Uint8Array)) throw invalidBody();
      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        await reader.cancel().catch(() => {});
        throw bodyTooLarge();
      }
      chunks.push(value);
    }
  } catch (error) {
    if (error instanceof BoundedJsonBodyError) throw error;
    throw invalidBody();
  }

  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return JSON.parse(text);
  } catch {
    throw invalidBody();
  }
}

function boundedJsonErrorResponse(error) {
  const boundedError = error instanceof BoundedJsonBodyError
    ? error
    : invalidBody();
  return Response.json(
    {
      error: {
        code: boundedError.code,
        message: boundedError.message,
      },
    },
    {
      status: boundedError.status,
      headers: { "Cache-Control": "no-store" },
    },
  );
}

export {
  BoundedJsonBodyError,
  boundedJsonErrorResponse,
  readBoundedJson,
};
