from __future__ import annotations

import asyncio
import os

import discord


async def delete_message(token: str, channel_id: int, message_id: int) -> None:
    client = discord.Client(intents=discord.Intents.none())

    @client.event
    async def on_ready() -> None:
        try:
            channel = await client.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
            await message.delete()
            print(f"Deleted message {message_id} from channel {channel_id}.")
        except discord.NotFound:
            print("The channel or message was not found.")
        except discord.Forbidden:
            print("The bot lacks permission to read or delete that message.")
        except discord.HTTPException as error:
            print(f"Discord API error: {error}")
        finally:
            await client.close()

    await client.start(token)


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_BOT_TOKEN before running this script.")

    try:
        channel_id = int(input("Channel ID: ").strip())
        message_id = int(input("Message ID: ").strip())
    except ValueError:
        raise SystemExit("Channel ID and message ID must be numeric.") from None

    confirmation = input(
        f"Type DELETE to delete message {message_id} from channel {channel_id}: "
    )
    if confirmation != "DELETE":
        raise SystemExit("Deletion cancelled.")

    asyncio.run(delete_message(token, channel_id, message_id))


if __name__ == "__main__":
    main()