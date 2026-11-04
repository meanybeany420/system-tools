-- Staged monitor transitions across rendered frames.
-- Hyprland Lua API (0.57+). Loading this module changes no monitor rules.
-- Only display-menu calls apply(); no config import or perpetual enforcement.
local valid = { auto=true, laptop=true, external=true, right=true, left=true, mirror=true }
local state = { phase="idle", mode="auto", attempts=0 }
local timer

local function snapshots()
    local all, active = {}, {}
    for _, mon in ipairs(hl.get_monitors({ all=true })) do
        all[mon.name] = mon
        if mon.enabled and mon.width > 0 and mon.height > 0 then
            active[mon.name] = mon
        end
        -- Also handle APIs that return mirrors only through their source.
        for _, mirror in ipairs(mon.mirrors or {}) do
            all[mirror.name] = mirror
            if mirror.enabled and mirror.width > 0 and mirror.height > 0 then
                active[mirror.name] = mirror
            end
        end
    end
    return all, active
end

local function rule(output, scale, position, disabled, mirror)
    if not output or output == "" then return end
    hl.monitor({ output=output, mode="preferred", scale=scale, position=position,
                 disabled=disabled, mirror=mirror or "", transform=0 })
end

local function primary(position)
    rule(state.laptop, state.laptop_scale, position or "0x0", false)
end

local function external(position, disabled, mirror)
    if state.connected then
        rule(state.external, state.external_scale, position, disabled, mirror)
    end
end

local function fail(message)
    state.phase, state.error = "failed", message
    timer:set_enabled(false)
    -- Do not disable a potentially working external screen on failed recovery.
    primary()
end

local function finalize(monitors)
    local a, b = monitors[state.laptop], monitors[state.external]
    local aw = math.floor(a.width / a.scale + 0.5)
    local bw = b and math.floor(b.width / b.scale + 0.5) or 0
    local mode = state.mode
    if not state.connected or mode == "laptop" then
        primary()
        external(aw .. "x0", true)
    elseif mode == "auto" or mode == "external" then
        -- Both outputs were independently verified active on the previous frame.
        external("0x0", false)
        rule(state.laptop, state.laptop_scale, "0x0", true)
    elseif mode == "mirror" then
        primary()
        external("0x0", false, state.laptop)
    elseif mode == "left" then
        primary(bw .. "x0")
        external("0x0", false)
    else
        primary()
        external(aw .. "x0", false)
    end
    state.phase, state.attempts = "settling", 0
end

local function matches(monitors)
    local a, b = monitors[state.laptop], monitors[state.external]
    local mode = state.mode
    if not state.connected or mode == "laptop" then
        return a and not a.is_mirror and not b
    elseif mode == "auto" or mode == "external" then
        return b and not b.is_mirror and not a
    elseif not a or not b or a.is_mirror then
        return false
    elseif mode == "mirror" then
        return b.is_mirror
    elseif b.is_mirror or a.y ~= b.y then
        return false
    elseif mode == "left" then
        return math.abs(b.x + b.width / b.scale - a.x) <= 1
    else
        return math.abs(a.x + a.width / a.scale - b.x) <= 1
    end
end

local function tick()
    if state.phase ~= "preparing" and state.phase ~= "settling" then return end
    local all, active = snapshots()
    if not all[state.laptop] then
        state.phase, state.error = "failed", "primary output disconnected"
        timer:set_enabled(false)
        return
    end
    for name, mon in pairs(all) do
        if name ~= state.laptop and name ~= state.external or (mon.transform or 0) ~= 0 then
            fail("outputs changed during transition; retry display-menu")
            return
        end
    end
    if state.connected and not all[state.external] then
        state.connected, state.phase, state.attempts = false, "preparing", 0
        primary()
        return
    end
    if state.phase == "preparing" then
        local a, b = active[state.laptop], active[state.external]
        if a and not a.is_mirror and (not state.connected or (b and not b.is_mirror)) then
            finalize(active)
            return
        end
    elseif matches(active) then
        state.phase = "ready"
        timer:set_enabled(false)
        return
    end
    state.attempts = state.attempts + 1
    if state.attempts >= 24 then fail("display did not reach requested state") end
end

return {
    apply = function(options)
        assert(valid[options.mode], "invalid display mode")
        assert(options.laptop and options.laptop ~= "", "primary output required")
        local all = snapshots()
        assert(all[options.laptop], "primary output disconnected")
        for name, mon in pairs(all) do
            assert(name == options.laptop or name == options.external, "unexpected extra output")
            assert((mon.transform or 0) == 0, "rotated outputs unsupported")
        end
        if not timer then timer = hl.timer(tick, {timeout=250, type="repeat"}) end
        state = {phase="preparing", mode=options.mode, laptop=options.laptop,
                 external=options.external, laptop_scale=options.laptop_scale,
                 external_scale=options.external_scale,
                 connected=all[options.external] ~= nil, attempts=0}
        -- Independent, non-mirrored screens must work before disabling either.
        primary()
        external("auto", false)
        timer:set_enabled(true)
    end,
    status = function()
        return state.phase .. ":" .. state.mode .. (state.error and ":" .. state.error or "")
    end,
}
