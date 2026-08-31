cmake_minimum_required(VERSION 3.20)
project(aoe4_bot LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Build types
set(CMAKE_CXX_FLAGS_DEBUG "/MDd /Zi /Ob0 /Od /RTC1")
set(CMAKE_CXX_FLAGS_RELEASE "/MD /O2 /Ob2 /DNDEBUG")

# Windows specific
if(WIN32)
    add_compile_definitions(WIN32_LEAN_AND_MEAN NOMINMAX _CRT_SECURE_NO_WARNINGS)
    link_libraries(psapi version)
endif()

# Output directories
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)

add_executable(aoe4_bot
    src/main.cpp
    src/memory/process.cpp
    src/memory/pattern_scanner.cpp
    src/memory/pointer_chain.cpp
    src/game/entity_list.cpp
    src/game/entity.cpp
    src/game/offsets.cpp
    src/bot/bot.cpp
    src/bot/state_machine.cpp
    src/utils/logger.cpp
)

target_include_directories(aoe4_bot PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

# Install
install(TARGETS aoe4_bot DESTINATION bin)