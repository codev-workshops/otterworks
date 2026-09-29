package com.otterworks.notification.model

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonTransformingSerializer
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.serializer
import java.time.Instant

/**
 * Accepts either an RFC 3339 string or a Unix epoch number (seconds or milliseconds,
 * as emitted by legacy producers) and normalizes it to an RFC 3339 string.
 */
object FlexibleTimestampSerializer : JsonTransformingSerializer<String>(serializer<String>()) {
    private const val EPOCH_MILLIS_THRESHOLD = 100_000_000_000L

    override fun transformDeserialize(element: JsonElement): JsonElement {
        if (element !is JsonPrimitive || element.isString) return element
        val epoch = element.longOrNull ?: return element
        val instant = if (epoch >= EPOCH_MILLIS_THRESHOLD) {
            Instant.ofEpochMilli(epoch)
        } else {
            Instant.ofEpochSecond(epoch)
        }
        return JsonPrimitive(instant.toString())
    }
}
