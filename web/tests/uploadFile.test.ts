import test from 'node:test'
import assert from 'node:assert/strict'

import client from '../src/api/client.ts'
import { uploadFile } from '../src/api/media.ts'

test('uploadFile maps backend downloadUrl to frontend url', async () => {
  const originalPost = client.post

  client.post = (async () => ({
    data: {
      code: 0,
      downloadUrl: 'https://example.com/media/photo.png',
      mimeType: 'image/png',
    },
  })) as typeof client.post

  try {
    const result = await uploadFile(
      new File(['img'], 'photo.png', { type: 'image/png' }),
      'session-1',
    )

    assert.equal(result.url, 'https://example.com/media/photo.png')
    assert.equal(result.mimeType, 'image/png')
  } finally {
    client.post = originalPost
  }
})
