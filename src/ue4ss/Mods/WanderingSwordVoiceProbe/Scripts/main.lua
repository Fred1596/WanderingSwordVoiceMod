-- Wandering Sword Voice Mod - native dialogue event bridge
-- Captures only the game's confirmed dialogue widgets. No OCR and no PAK changes.

local TAG = "[WSVOICE]"
local DIALOGUE_WIDGET = "BPMV_DialogModuleView"
local DIALOGUE_NARRATION_WIDGET = "BPVE_DialogModule_Narration"
local FULLSCREEN_SCROLL_WIDGET = "BPMV_FullscreenScroll"
local NAME_WIDGET_SUFFIX = ".TXT_Name"
local CONTENT_WIDGET_SUFFIX = ".TXT_Cont"
local SCROLL_CONTENT_WIDGET_SUFFIX = ".RTXT_Cont"
local STABLE_DELAY_MS = 350

local SYSTEM_NARRATOR_TEXTS = {
    ["即将进入剧情战斗，建议少侠及时调整战斗模式。"] = true,
}

local current_speaker = ""
local current_content = ""
local content_sequence = 0
local last_emitted_key = ""

local function json_escape(value)
    value = tostring(value or "")
    return value:gsub('[%z\1-\31\\"]', function(char)
        local replacements = {
            ['"'] = '\\"',
            ['\\'] = '\\\\',
            ['\b'] = '\\b',
            ['\f'] = '\\f',
            ['\n'] = '\\n',
            ['\r'] = '\\r',
            ['\t'] = '\\t',
        }
        return replacements[char] or string.format("\\u%04x", string.byte(char))
    end)
end

local function trim(value)
    return (tostring(value or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

local function clean_rich_text(value)
    value = tostring(value or "")
    value = value:gsub("<[^>]*>", "")
    value = value:gsub("#nl", "\n")
    value = value:gsub("\\n", "\n")
    return trim(value)
end

local function ends_with(value, suffix)
    return suffix == "" or value:sub(-#suffix) == suffix
end

local function unwrap(value)
    if value == nil then return nil end
    local ok, unwrapped = pcall(function() return value:get() end)
    if ok then return unwrapped end
    return value
end

local function value_to_string(value)
    value = unwrap(value)
    if value == nil then return nil end
    if type(value) == "string" then return value end
    local ok, converted = pcall(function() return value:ToString() end)
    if ok and type(converted) == "string" then return converted end
    return nil
end

local function object_full_name(value)
    local object = unwrap(value)
    if object == nil then return nil end
    local valid_ok, is_valid = pcall(function() return object:IsValid() end)
    if not valid_ok or not is_valid then return nil end
    local name_ok, full_name = pcall(function() return object:GetFullName() end)
    if not name_ok then return nil end
    return tostring(full_name)
end

local function resolve_output_path()
    local ok, directories = pcall(IterateGameDirectories)
    if ok and directories and directories.Game and directories.Game.Binaries
        and directories.Game.Binaries.Win64 then
        local node = directories.Game.Binaries.Win64
        if type(node.__absolute_path) == "string" then
            return node.__absolute_path
                .. "\\ue4ss\\Mods\\WanderingSwordVoiceProbe\\dialogue_events.jsonl"
        end
    end
    return ".\\ue4ss\\Mods\\WanderingSwordVoiceProbe\\dialogue_events.jsonl"
end

local output_path = resolve_output_path()

local function append_line(line)
    local file, error_message = io.open(output_path, "a")
    if not file then
        print(string.format("%s cannot open event file: %s (%s)\n", TAG,
            output_path, tostring(error_message)))
        return false
    end
    file:write(line, "\n")
    file:close()
    return true
end

local function append_session_event()
    local line = string.format(
        '{"time":"%s","event":"bridge_started","version":3}',
        os.date("!%Y-%m-%dT%H:%M:%SZ")
    )
    append_line(line)
    print(string.format("%s native dialogue bridge ready: %s\n", TAG, output_path))
end

local function emit_dialogue(sequence, captured_speaker, captured_content, source)
    if sequence ~= content_sequence then return end

    local speaker = trim(captured_speaker)
    local text = clean_rich_text(captured_content)
    if text == "" then return end
    if speaker == "" then speaker = "旁白" end

    local dedupe_key = speaker .. "\0" .. text
    if dedupe_key == last_emitted_key then return end
    last_emitted_key = dedupe_key

    local line = string.format(
        '{"time":"%s","event":"dialogue","source":"%s","speaker":"%s","text":"%s"}',
        os.date("!%Y-%m-%dT%H:%M:%SZ"),
        json_escape(source or "dialogue"),
        json_escape(speaker),
        json_escape(text)
    )
    append_line(line)
    print(string.format("%s dialogue | %s | %s\n", TAG, speaker, text))
end

local function observe_set_text(context, in_text)
    local full_name = object_full_name(context)
    if not full_name then return end

    local is_dialogue = full_name:find(DIALOGUE_WIDGET, 1, true)
        or full_name:find(DIALOGUE_NARRATION_WIDGET, 1, true)
    local is_fullscreen_scroll = full_name:find(FULLSCREEN_SCROLL_WIDGET, 1, true)
    if not is_dialogue and not is_fullscreen_scroll then return end

    local text = value_to_string(in_text)
    if text == nil then return end

    if is_dialogue and ends_with(full_name, NAME_WIDGET_SUFFIX) then
        current_speaker = trim(text)
        return
    end

    local is_dialogue_content = is_dialogue
        and ends_with(full_name, CONTENT_WIDGET_SUFFIX)
    local is_scroll_content = is_fullscreen_scroll
        and ends_with(full_name, SCROLL_CONTENT_WIDGET_SUFFIX)
    if is_dialogue_content or is_scroll_content then
        current_content = text
        local captured_content = text
        local cleaned_content = clean_rich_text(text)
        local source = "dialogue"
        local captured_speaker = current_speaker
        if is_scroll_content then
            source = "narration"
            captured_speaker = "旁白"
        elseif SYSTEM_NARRATOR_TEXTS[cleaned_content] then
            source = "system"
            captured_speaker = "旁白"
        end
        content_sequence = content_sequence + 1
        local sequence = content_sequence
        ExecuteWithDelay(STABLE_DELAY_MS, function()
            emit_dialogue(
                sequence,
                captured_speaker,
                captured_content,
                source
            )
        end)
    end
end

local function register_text_hook(function_path)
    local ok, pre_id, post_id = pcall(RegisterHook, function_path, observe_set_text)
    if ok then
        print(string.format("%s hook registered: %s (%s, %s)\n", TAG,
            function_path, tostring(pre_id), tostring(post_id)))
    else
        print(string.format("%s hook unavailable: %s (%s)\n", TAG,
            function_path, tostring(pre_id)))
    end
end

append_session_event()
register_text_hook("/Script/UMG.TextBlock:SetText")
register_text_hook("/Script/UMG.RichTextBlock:SetText")
