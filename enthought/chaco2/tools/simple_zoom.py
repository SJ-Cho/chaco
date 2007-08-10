""" Defines the SimpleZoom class.
"""
from numpy import allclose, array, inf

# Enthought library imports
from enthought.enable2.api import ColorTrait
from enthought.traits.api \
    import Enum, false, Float, Instance, Int, Str, Trait, true, Tuple

# Chaco imports
from enthought.chaco2.api import AbstractOverlay, KeySpec
from tool_history_mixin import ToolHistoryMixin

class SimpleZoom(AbstractOverlay, ToolHistoryMixin):
    """ Selects a range along the index or value axis. 
    
    The user left-click-drags to select a region to zoom in.  
    Certain keyboard keys are mapped to performing zoom actions as well.
    
    Implements a basic "zoom stack" so the user move go backwards and forwards
    through previous zoom regions.
    """
    
    # The selection mode:
    #
    # range:
    #   Select a range across a single index or value axis.
    # box: 
    #   Perform a "box" selection on two axes.
    tool_mode = Enum("box", "range")
    
    # Is the tool always "on"? If True, left-clicking always initiates
    # a zoom operation; if False, the user must press a key to enter zoom mode.
    always_on = false

    #-------------------------------------------------------------------------
    # Zoom control
    #-------------------------------------------------------------------------

    # The axis to which the selection made by this tool is perpendicular. This
    # only applies in 'range' mode.
    axis = Enum("index", "value")

    # The maximum ratio between the original data space bounds and the zoomed-in
    # data space bounds.  If None, then there is no limit (not advisable!).
    max_zoom_in_factor = Float(1e5, allow_none=True)

    # The maximum ratio between the zoomed-out data space bounds and the original
    # bounds.  If None, then there is no limit.
    max_zoom_out_factor = Float(1e5, allow_none=True)
    
    #-------------------------------------------------------------------------
    # Interaction control
    #-------------------------------------------------------------------------
    
    # Enable the mousewheel for zooming?
    enable_wheel = true

    # The mouse button that initiates the drag.
    drag_button = Enum("left", "right")
    
    # Conversion ratio from wheel steps to zoom factors.
    wheel_zoom_step = Float(1.0)
    
    # The key press to enter zoom mode, if **always_on** is False.  Has no effect
    # if **always_on** is True.
    enter_zoom_key = Instance(KeySpec, args=("z",))
    
    # The key press to leave zoom mode, if **always_on** is False.  Has no effect
    # if **always_on** is True.
    exit_zoom_key = Instance(KeySpec, args=("z",))

    # Disable the tool after the zoom is completed?
    disable_on_complete = true
    
    # The minimum amount of screen space the user must select in order for
    # the tool to actually take effect.
    minimum_screen_delta = Int(10)
    
    #-------------------------------------------------------------------------
    # Appearance properties (for Box mode)
    #-------------------------------------------------------------------------

    # The pointer to use when drawing a zoom box.
    pointer = "magnifier"
    
    # The color of the selection box.
    color = ColorTrait("lightskyblue")
    
    # The alpha value to apply to **color** when filling in the selection
    # region.  Because it is almost certainly useless to have an opaque zoom
    # rectangle, but it's also extremely useful to be able to use the normal
    # named colors from Enable, this attribute allows the specification of a 
    # separate alpha value that replaces the alpha value of **color** at draw
    # time.
    alpha = Trait(0.4, None, Float)
    
    # The color of the outside selection rectangle.
    border_color = ColorTrait("dodgerblue")

    # The thickness of selection rectangle border.
    border_size = Int(1)
    
    # The possible event states of this zoom tool.
    event_state = Enum("normal", "selecting")
    
    #------------------------------------------------------------------------
    # Key mappings
    #------------------------------------------------------------------------

    # The key that cancels the zoom and resets the view to the original defaults.
    cancel_zoom_key = Instance(KeySpec, args=("Esc",))

    #------------------------------------------------------------------------
    # Private traits
    #------------------------------------------------------------------------

    # If **always_on** is False, this attribute indicates whether the tool
    # is currently enabled.
    _enabled = false

    # the original numerical screen ranges 
    _orig_low_setting = Trait(None, Tuple, Float, Str)
    _orig_high_setting = Trait(None, Tuple, Float, Str)

    # The (x,y) screen point where the mouse went down.
    _screen_start = Trait(None, None, Tuple)
    
    # The (x,,y) screen point of the last seen mouse move event.
    _screen_end = Trait(None, None, Tuple)

    def __init__(self, component=None, *args, **kw):
        # Support AbstractController-style constructors so that this can be
        # handed in the component it will be overlaying in the constructor
        # without using kwargs.
        self.component = component
        super(SimpleZoom, self).__init__(*args, **kw)
        self._reset_state_to_current()
        if self.tool_mode == "range":
            mapper = self._get_mapper()
            self._orig_low_setting = mapper.range.low_setting
            self._orig_high_setting = mapper.range.high_setting
        else:
            x_range = self.component.x_mapper.range
            y_range = self.component.y_mapper.range
            self._orig_low_setting = (x_range.low_setting, y_range.low_setting) 
            self._orig_high_setting = \
                (x_range.high_setting, y_range.high_setting)
        component.on_trait_change(self._reset_state_to_current,
                                  "index_data_changed")
        return

    def enable(self, event=None):
        """ Provides a programmatic way to enable this tool, if 
        **always_on** is False.  
        
        Calling this method has the same effect as if the user pressed the
        **enter_zoom_key**.
        """
        if self.component.active_tool != self:
            self.component.active_tool = self
        self._enabled = True
        if event and event.window:
            event.window.set_pointer(self.pointer)
        return
    
    def disable(self, event=None):
        """ Provides a programmatic way to enable this tool, if **always_on**
        is False.  
        
        Calling this method has the same effect as if the user pressed the 
        **exit_zoom_key**.
        """
        self.reset()
        self._enabled = False
        if self.component.active_tool == self:
            self.component.active_tool = None
        if event and event.window:
            event.window.set_pointer("arrow")
        return

    def reset(self, event=None):
        """ Resets the tool to normal state, with no start or end position.
        """
        self.event_state = "normal"
        self._screen_start = None
        self._screen_end = None
        
    
    def deactivate(self, component):
        """ Called when this is no longer the active tool.
        """
        # Required as part of the AbstractController interface.
        return self.disable()
    
    def overlay(self, component, gc, view_bounds=None, mode="normal"):
        """ Draws this component overlaid on another component.
        
        Overrides AbstractOverlay.
        """
        if self.event_state == "selecting":
            if self.tool_mode == "range":
                self.overlay_range(component, gc)
            else:
                self.overlay_box(component, gc)
        return

    def overlay_box(self, component, gc):
        """ Draws the overlay as a box.
        """
        if self._screen_start and self._screen_end:
            gc.save_state()
            try:
                gc.set_antialias(0)
                gc.set_line_width(self.border_size)
                gc.set_stroke_color(self.border_color_)
                gc.clip_to_rect(component.x, component.y, component.width, component.height)
                x, y = self._screen_start
                x2, y2 = self._screen_end
                rect = (x, y, x2-x+1, y2-y+1)
                if self.color != "transparent":
                    if self.alpha:
                        color = list(self.color_)
                        if len(color) == 4:
                            color[3] = self.alpha
                        else:
                            color += [self.alpha]
                    else:
                        color = self.color_
                    gc.set_fill_color(color)
                    gc.rect(*rect)
                    gc.draw_path()
                else:
                    gc.rect(*rect)
                    gc.stroke_path()
            finally:
                gc.restore_state()
        return

    def overlay_range(self, component, gc):
        """ Draws the overlay as a range.
        """
        axis_ndx = self._determine_axis()
        lower_left = [0,0]
        upper_right = [0,0]
        lower_left[axis_ndx] = self._screen_start[axis_ndx]
        lower_left[1-axis_ndx] = self.component.position[1-axis_ndx]
        upper_right[axis_ndx] = self._screen_end[axis_ndx] - self._screen_start[axis_ndx]
        upper_right[1-axis_ndx] = self.component.bounds[1-axis_ndx]
        
        gc.save_state()
        try:
            gc.set_antialias(0)
            gc.set_alpha(self.alpha)
            gc.set_fill_color(self.color_)
            gc.set_stroke_color(self.border_color_)
            gc.clip_to_rect(component.x, component.y, component.width, component.height)
            gc.rect(lower_left[0], lower_left[1], upper_right[0], upper_right[1])
            gc.draw_path()
        finally:
            gc.restore_state()
        return
    
    def normal_left_down(self, event):
        """ Handles the left mouse button being pressed while the tool is
        in the 'normal' state.
        
        If the tool is enabled or always on, it starts selecting.
        """
        if self.always_on or self._enabled:
            # we need to make sure that there isn't another active tool that we will
            # interfere with.
            if self.drag_button == "left":
                self._start_select(event)
        return

    def normal_right_down(self, event):
        """ Handles the right mouse button being pressed while the tool is
        in the 'normal' state.
        
        If the tool is enabled or always on, it starts selecting.
        """
        if self.always_on or self._enabled:
            if self.drag_button == "right":
                self._start_select(event)
        return

    def selecting_mouse_move(self, event):
        """ Handles the mouse moving when the tool is in the 'selecting' state.
        
        The selection is extended to the current mouse position.
        """
        self._screen_end = (event.x, event.y)
        self.component.request_redraw()
        event.handled = True
        return
    
    def selecting_left_up(self, event):
        """ Handles the left mouse button being released when the tool is in
        the 'selecting' state.
        
        Finishes selecting and does the zoom.
        """
        if self.drag_button == "left":
            self._end_select(event)
        return

    def selecting_right_up(self, event):
        """ Handles the right mouse button being released when the tool is in 
        the 'selecting' state.
        
        Finishes selecting and does the zoom.
        """
        if self.drag_button == "right":
            self._end_select(event)
        return

    def selecting_mouse_leave(self, event):
        """ Handles the mouse leaving the plot when the tool is in the 
        'selecting' state.
        
        Ends the selection operation without zooming.
        """
        self._end_selecting(event)
        return

    def selecting_key_pressed(self, event):
        """ Handles a key being pressed when the tool is in the 'selecting'
        state.
        
        If the key pressed is the **cancel_zoom_key**, then selecting is 
        canceled.
        """
        if self.cancel_zoom_key.match(event):
            self._end_selecting(event)
            event.handled = True
        return

    def _start_select(self, event):
        """ Starts selecting the zoom region
        """
        if self.component.active_tool in (None, self):
            self.component.active_tool = self
        else:
            self._enabled = False
        self._screen_start = (event.x, event.y)
        self._screen_end = None
        self.event_state = "selecting"
        event.window.set_pointer(self.pointer)
        event.window.set_mouse_owner(self, event.net_transform())
        self.selecting_mouse_move(event)
        return


    def _end_select(self, event):
        """ Ends selection of the zoom region, adds the new zoom range to
        the zoom stack, and does the zoom.
        """
        self._screen_end = (event.x, event.y)

        start = array(self._screen_start)
        end = array(self._screen_end)
        
        if sum(abs(end - start)) < self.minimum_screen_delta:
            self._end_selecting(event)
            event.handled = True
            return
        
        if self.tool_mode == "range":
            mapper = self._get_mapper()
            axis = self._determine_axis()
            low = mapper.map_data(self._screen_start[axis])
            high = mapper.map_data(self._screen_end[axis])
            
            if low > high:
                low, high = high, low
        else:
            low, high = self._map_coordinate_box(self._screen_start, self._screen_end)

        new_zoom_range = (low, high)
        self._append_state(new_zoom_range)
        self._do_zoom()
        self._end_selecting(event)
        event.handled = True
        return
    
    def _end_selecting(self, event=None):
        """ Ends selection of zoom region, without zooming.
        """
        if self.disable_on_complete:
            self.disable(event)
        else:
            self.reset()
        self.component.request_redraw()
        if event and event.window.mouse_owner == self:
            event.window.set_mouse_owner(None)
        return

    def _zoom_limit_reached(self, orig_low, orig_high, new_low, new_high):
        """ Returns True if the new low and high exceed the maximum zoom
        limits
        """
        orig_bounds = orig_high - orig_low

        if orig_bounds == inf:
            # There isn't really a good way to handle the case when the
            # original bounds were infinite, since any finite zoom
            # range will certainly exceed whatever zoom factor is set.
            # In this case, we just allow unbounded levels of zoom.
            return False
        
        new_bounds = new_high - new_low
        if allclose(orig_bounds, 0.0):
            return True
        if allclose(new_bounds, 0.0):
            return True
        if (new_bounds / orig_bounds) > self.max_zoom_out_factor or \
           (orig_bounds / new_bounds) > self.max_zoom_in_factor:
            return True
        return False
    
    def _do_zoom(self):
        """ Does the zoom operation.
        """
        # Sets the bounds on the component using _cur_stack_index
        low, high = self._current_state()
        orig_low, orig_high = self._history[0]

        if self._history_index == 0:
            if self.tool_mode == "range":
                mapper = self._get_mapper()
                mapper.range.low_setting = self._orig_low_setting
                mapper.range.high_setting = self._orig_high_setting
            else:
                x_range = self.component.x_mapper.range
                y_range = self.component.y_mapper.range
                x_range.low_setting, y_range.low_setting = \
                    self._orig_low_setting
                x_range.high_setting, y_range.high_setting = \
                    self._orig_high_setting
                
        else:    
            if self.tool_mode == "range":
                if self._zoom_limit_reached(orig_low, orig_high, low, high):
                    self._pop_state()
                    return
                mapper = self._get_mapper()
                mapper.range.low = low
                mapper.range.high = high
            else:
                for ndx in (0, 1):
                    if self._zoom_limit_reached(orig_low[ndx], orig_high[ndx],
                                                low[ndx], high[ndx]):
                        # pop _current_state off the stack and leave the actual
                        # bounds unmodified.
                        self._pop_state()
                        return
                x_range = self.component.x_mapper.range
                y_range = self.component.y_mapper.range
                x_range.low, y_range.low = low
                x_range.high, y_range.high = high
            
        self.component.request_redraw()
        return

    def normal_key_pressed(self, event):
        """ Handles a key being pressed when the tool is in 'normal' state.
        
        If the tool is not always on, this method handles turning it on and
        off when the appropriate keys are pressed. Also handles keys to 
        manipulate the tool history.
        """
        if not self.always_on:
            if not self._enabled and self.enter_zoom_key.match(event):
                if self.component.active_tool in (None, self):
                    self.component.active_tool = self
                    self._enabled = True
                    event.window.set_pointer(self.pointer)
                else:
                    self._enabled = False
                return
            elif self._enabled and self.exit_zoom_key.match(event):
                self._enabled = False
                event.window.set_pointer("arrow")
                return
            
        self._history_handle_key(event)
        
        if event.handled:
            self.component.request_redraw()
        return

    def normal_mouse_wheel(self, event):
        """ Handles the mouse wheel being used when the tool is in the 'normal'
        state.
        
        Scrolling the wheel "up" zooms in; scrolling it "down" zooms out.
        """
        if self.enable_wheel and event.mouse_wheel != 0:
            if event.mouse_wheel > 0:
                # zoom in
                zoom = 1.0 / (1.0 + 0.5 * self.wheel_zoom_step)
            elif event.mouse_wheel < 0:
                # zoom out
                zoom = 1.0 + 0.5 * self.wheel_zoom_step
            
            # We'll determine the current position of the cursor in dataspace,
            # then zoom in while trying to maintain the mouse screen coordinates
            # in the new range.
            c = self.component
            low_pt, high_pt = self._map_coordinate_box((c.x, c.y), (c.x2, c.y2))
            mouse_pos = (c.x_mapper.map_data(event.x), c.y_mapper.map_data(event.y))
            
            if self.tool_mode == "range":
                datarange_list = [(self._determine_axis(), self._get_mapper().range)]
            else:
                datarange_list = [(0, c.x_mapper.range), (1, c.y_mapper.range)]
            
            orig_low, orig_high = self._history[0]
            for ndx, datarange in datarange_list:
                mouse_val = mouse_pos[ndx]
                newlow = mouse_val - zoom * (mouse_val - low_pt[ndx])
                newhigh = mouse_val + zoom * (high_pt[ndx] - mouse_val)
                
                if type(orig_high) in (tuple,list):
                    ol, oh = orig_low[ndx], orig_high[ndx]
                else:
                    ol, oh = orig_low, orig_high
                
                if self._zoom_limit_reached(ol, oh, newlow, newhigh):
                    event.handled = True
                    return
                
                datarange.set_bounds(newlow, newhigh)
            event.handled = True
            self.component.request_redraw()
        return

    def _component_changed(self):
        if self._get_mapper() is not None:
            self._reset_state_to_current()
        return

    #------------------------------------------------------------------------
    # Implementation of PlotComponent interface
    #------------------------------------------------------------------------
    def _activate(self):
        """ Called by PlotComponent to set this as the active tool.
        """
        self.enable()
    
    #------------------------------------------------------------------------
    # implementations of abstract methods on ToolHistoryMixin
    #------------------------------------------------------------------------
    def _reset_state_to_current(self):
        """ Clears the tool history, and sets the current state to be the
        first state in the history.
        """
        if self.tool_mode == "range":
            mapper = self._get_mapper()
            self._reset_state((mapper.range.low, 
                               mapper.range.high))
        else:
            x_range = self.component.x_mapper.range
            y_range = self.component.y_mapper.range
            self._reset_state(((x_range.low, y_range.low), 
                               (x_range.high, y_range.high)))

    def _reset_state_pressed(self):
        """ Called when the tool needs to reset its history.  
        
        The history index will have already been set to 0. Implements
        ToolHistoryMixin.
        """
        # First zoom to the set state (ZoomTool handles setting the index=0).
        self._do_zoom()

        # Now reset the state to the current bounds settings.
        self._reset_state_to_current()
        return

    def _prev_state_pressed(self):
        """ Called when the tool needs to advance to the previous state in the
        stack.
        
        The history index will have already been set to the index corresponding
        to the prev state. Implements ToolHistoryMixin.
        """
        self._do_zoom()
        return
    
    def _next_state_pressed(self):
        """ Called when the tool needs to advance to the next state in the stack.
        
        The history index will have already been set to the index corresponding
        to the next state. Implements ToolHistoryMixin.
        """
        self._do_zoom()
        return
    
    #------------------------------------------------------------------------
    # Utility methods for computing axes, coordinates, etc.
    #------------------------------------------------------------------------

    def _get_mapper(self):
        """ Returns the mapper for the component associated with this tool.
        
        The zoom tool really only cares about this, so subclasses can easily
        customize SimpleZoom to work with all sorts of components just by
        overriding this method.
        """
        return getattr(self.component, self.axis + "_mapper")
        

    def _get_axis_coord(self, event, axis="index"):
        """ Returns the coordinate of the event along the axis of interest
        to the tool (or along the orthogonal axis, if axis="value").
        """
        event_pos = (event.x, event.y)
        if axis == "index":
            return event_pos[ self._determine_axis() ]
        else:
            return event_pos[ 1 - self._determine_axis() ]

    def _determine_axis(self):
        """ Determines whether the index of the coordinate along the axis of
        interest is the first or second element of an (x,y) coordinate tuple.
        """
        if self.axis == "index":
            if self.component.orientation == "h":
                return 0
            else:
                return 1
        else:   # self.axis == "value"
            if self.component.orientation == "h":
                return 1
            else:
                return 0

    def _map_coordinate_box(self, start, end):
        """ Given start and end points in screen space, returns corresponding
        low and high points in data space.
        """
        low = [0,0]
        high = [0,0]
        for axis_index, mapper in [(0, self.component.x_mapper), \
                                   (1, self.component.y_mapper)]:
            low_val = mapper.map_data(start[axis_index])
            high_val = mapper.map_data(end[axis_index])
            
            if low_val > high_val:
                low_val, high_val = high_val, low_val
            low[axis_index] = low_val
            high[axis_index] = high_val
        return low, high
    
    ### Persistence ###########################################################

    def __getstate__(self):
        dont_pickle = [
            'always_on',
            'enter_zoom_key',    
            'exit_zoom_key',
            'minimum_screen_delta',
            'event_state',
            'reset_zoom_key',
            'prev_zoom_key',
            'next_zoom_key',
            'pointer',
            '_enabled',
            '_screen_start',
            '_screen_end']
        state = super(SimpleZoom,self).__getstate__()
        for key in dont_pickle:
            if state.has_key(key):
                del state[key]

        return state

# EOF