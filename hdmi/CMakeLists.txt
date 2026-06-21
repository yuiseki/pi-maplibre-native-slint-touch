# CMake integration for the zero-copy GL app (`maplibre-slint-gl`).
#
# This is NOT a standalone project. The target depends on `Slint::Slint`,
# `mbgl-core`, `cpr::cpr` and the maplibre-native vendor includes, which only
# exist inside a maplibre/maplibre-native-slint build. The app is upstreamed as
# maplibre/maplibre-native-slint#68, where this exact block lives in
# `cpp/CMakeLists.txt`.
#
# To build, use `scripts/build.sh` (it checks out maplibre-native-slint, overlays
# the sources in this directory onto its `cpp/`, and builds the target). This
# file is kept here as the canonical copy of the target definition.
#
# The guard makes it a no-op unless the OpenGL backend is available (so it is
# skipped on CI / WebGPU / Windows / macOS builds).

if(MLN_WITH_OPENGL AND NOT APPLE AND NOT WIN32)
    add_executable(maplibre-slint-gl
        main_gl.cpp
        src/slint_map_gl.cpp
        src/slint_gl_backend.cpp
        platform/custom_file_source.cpp
    )

    # This target has its own Slint UI (Pi layout); generates gl_map_window.h.
    if (COMMAND slint_target_sources)
        slint_target_sources(maplibre-slint-gl
            ${CMAKE_CURRENT_SOURCE_DIR}/gl_map_window.slint
        )
    else()
        message(FATAL_ERROR "slint_target_sources command is not available.")
    endif()

    target_include_directories(maplibre-slint-gl
        PRIVATE
            ${CMAKE_CURRENT_SOURCE_DIR}/src
            ${CMAKE_CURRENT_SOURCE_DIR}/platform
            ${CMAKE_SOURCE_DIR}/vendor/maplibre-native/include
            ${CMAKE_BINARY_DIR}/vendor/maplibre-native/include
            ${CMAKE_CURRENT_BINARY_DIR}
            ${GLES3_INCLUDE_DIRS}
    )

    # mbgl-core is built with -fno-rtti (MLN_WITH_RTTI=OFF); match it so the
    # SlintGLBackend subclass does not require base-class typeinfo symbols.
    target_compile_options(maplibre-slint-gl PRIVATE $<$<COMPILE_LANGUAGE:CXX>:-fno-rtti>)

    find_library(SLINT_GL_EGL_LIB EGL)

    target_link_libraries(maplibre-slint-gl
        PRIVATE
            Slint::Slint
            mbgl-core
            cpr::cpr
            ${GLES3_LIBRARIES}
            ${OPENGL_LIBRARIES}
    )

    if (SLINT_GL_EGL_LIB)
        target_link_libraries(maplibre-slint-gl PRIVATE ${SLINT_GL_EGL_LIB})
    else()
        target_link_libraries(maplibre-slint-gl PRIVATE EGL)
    endif()
else()
    message(STATUS "maplibre-slint-gl: skipped (requires MLN_WITH_OPENGL on Linux)")
endif()
