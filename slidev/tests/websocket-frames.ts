// The raw RFC 6455 frames the fake servers write and read.

export function closeFrame(code: number) {
  return Buffer.from([0x88, 0x02, code >> 8, code & 0xff])
}

// Whether a client's masked frames, which may arrive together, include a close frame.
export function hasCloseFrame(data: Buffer) {
  const CLOSE = 0x08
  const MASKED_HEADER = 6
  let offset = 0
  while (offset + 2 <= data.length) {
    if ((data.readUInt8(offset) & 0x0f) === CLOSE) return true
    const short = data.readUInt8(offset + 1) & 0x7f
    if (short === 126) offset += MASKED_HEADER + 2 + data.readUInt16BE(offset + 2)
    else if (short === 127) offset += MASKED_HEADER + 8 + Number(data.readBigUInt64BE(offset + 2))
    else offset += MASKED_HEADER + short
  }
  return false
}

// One unmasked text frame; RFC 6455 marks a payload of 126 bytes or more with 126
// and a 16-bit length.
export function frame(message: string) {
  const data = Buffer.from(message)
  const header = data.length < 126
    ? Buffer.from([0x81, data.length])
    : Buffer.from([0x81, 126, data.length >> 8, data.length & 0xff])
  return Buffer.concat([header, data])
}
